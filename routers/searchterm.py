import os
import re
import random
import asyncio
import httpx
import aiohttp
import trafilatura
import json
import uuid
import time
from pathlib import Path
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Body
from fastapi.responses import FileResponse
from newspaper import Article
from threading import Timer
from google import genai
from google.genai import types

router = APIRouter()

BRAVE_API_KEY = os.getenv("BRAVE_API_KEY")
if not BRAVE_API_KEY:
    raise ValueError("BRAVE_API_KEY não está definido!")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY não está definido!")

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
    "x-subscription-token": BRAVE_API_KEY
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

BLOCKED_DOMAINS = {"reddit.com", "www.reddit.com", "old.reddit.com",
                   "quora.com", "www.quora.com"}

MAX_TEXT_LENGTH = 4000

# Diretório para arquivos temporários
TEMP_DIR = Path("/tmp")
TEMP_DIR.mkdir(exist_ok=True)

# Dicionário para controlar arquivos temporários
temp_files = {}


def is_blocked_domain(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        return any(host == b or host.endswith("." + b) for b in BLOCKED_DOMAINS)
    except Exception:
        return False


def clamp_text(text: str) -> str:
    if not text:
        return ""
    if len(text) > MAX_TEXT_LENGTH:
        return text[:MAX_TEXT_LENGTH]
    return text


def get_realistic_headers() -> Dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.7,pt-BR;q=0.6",
        "Connection": "keep-alive",
    }


def delete_temp_file(file_id: str, file_path: Path):
    """Remove arquivo temporário após expiração"""
    try:
        if file_path.exists():
            file_path.unlink()
        temp_files.pop(file_id, None)
        print(f"Arquivo temporário removido: {file_path}")
    except Exception as e:
        print(f"Erro ao remover arquivo temporário: {e}")


def create_temp_file(data: Dict[str, Any]) -> Dict[str, str]:
    """Cria arquivo temporário e agenda sua remoção"""
    file_id = str(uuid.uuid4())
    file_path = TEMP_DIR / f"fontes_{file_id}.txt"
    
    # Salva o JSON no arquivo
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    # Agenda remoção em 24 horas (86400 segundos)
    timer = Timer(86400, delete_temp_file, args=[file_id, file_path])
    timer.start()
    
    # Registra o arquivo temporário
    temp_files[file_id] = {
        "path": file_path,
        "created_at": time.time(),
        "timer": timer
    }
    
    return {
        "file_id": file_id,
        "download_url": f"/download-temp/{file_id}",
        "expires_in_hours": 24
    }


async def generate_search_terms(context: str) -> List[str]:
    """Gera termos de pesquisa usando o modelo Gemini"""
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        model = "gemini-2.5-flash-lite"
        
        system_prompt = """Com base num contexto inicial, gere termos de pesquisa (até 10 termos, no máximo), em um JSON. Esses textos devem ser interpretados como termos que podem ser usados por outras inteligências artificiais pra pesquisar no google e retornar resultados mais refinados e completos pra busca atual. Analise muito bem o contexto, por exemplo, se está falando de uma série coreana, gere os termos em coreano por que obviamente na mídia coreana terá mais cobertura que a americana, etc.

Deve seguir esse formato: "terms": []

Retorne apenas o JSON, sem mais nenhum texto."""

        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text="Contexto: Taylor Sheridan's 'Landman' Announces Season 2 Premiere Date"),
                ],
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text='{"terms": [ "imdb landman episodes season 2", "imdb landman series", "landman season 2 release date", "taylor sheridan landman series", "landman season 2 cast sam elliott", "billy bob thornton returns landman", "demi moore landman new season", "andy garcia ali larter landman season 2", "landman texas oil drama", "taylor sheridan tv series schedule"]}'),
                ],
            ),
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text="Contexto: Pixar's latest animated feature will arrive on digital (via platforms like Apple TV, Amazon Prime Video, and Fandango at Home) on Aug. 19 and on physical media (4K UHD, Blu-ray and DVD) on Sept. 9. The film has not yet set a Disney+ streaming release date, but that will likely come after the Blu-ray release."),
                ],
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text='{ "terms": ["pixar elio 2024 movie details", "disney pixar new release elio", "elio animated film august 19 digital", "pixar sci-fi comedy elio home release", "elio movie blu-ray dvd release september", "where to watch elio online", "elio streaming disney plus release date", "elio digital release apple tv amazon prime", "elio physical media 4k uhd blu-ray dvd", "elio movie bonus features"] }'),
                ],
            ),
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=f"Contexto: {context}"),
                ],
            ),
        ]
        
        generate_content_config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                thinking_budget=0,
            ),
        )
        
        # Coletamos toda a resposta em stream
        full_response = ""
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=generate_content_config,
        ):
            if chunk.text:
                full_response += chunk.text
        
        # Tenta extrair o JSON da resposta
        try:
            # Remove possíveis ```json e ``` da resposta
            clean_response = full_response.strip()
            if clean_response.startswith("```json"):
                clean_response = clean_response[7:]
            if clean_response.endswith("```"):
                clean_response = clean_response[:-3]
            clean_response = clean_response.strip()
            
            # Parse do JSON
            response_data = json.loads(clean_response)
            terms = response_data.get("terms", [])
            
            # Validação básica
            if not isinstance(terms, list):
                raise ValueError("Terms deve ser uma lista")
            
            return terms[:20]  # Garante máximo de 20 termos
            
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Erro ao parsear resposta do Gemini: {e}")
            print(f"Resposta recebida: {full_response}")
            # Retorna uma lista vazia em caso de erro
            return []
        
    except Exception as e:
        print(f"Erro ao gerar termos de pesquisa: {str(e)}")
        return []


async def search_brave_term(client: httpx.AsyncClient, term: str) -> List[Dict[str, str]]:
    params = {"q": term, "count": 10, "safesearch": "off", "summary": "false"}
    
    try:
        resp = await client.get(BRAVE_SEARCH_URL, headers=BRAVE_HEADERS, params=params)
        if resp.status_code != 200:
            return []

        data = resp.json()
        results: List[Dict[str, str]] = []
        
        if "web" in data and "results" in data["web"]:
            for item in data["web"]["results"]:
                url = item.get("url")
                age = item.get("age", "Unknown")
                
                if url and not is_blocked_domain(url):
                    results.append({"url": url, "age": age})

        return results
    except Exception:
        return []


async def extract_article_text(url: str, session: aiohttp.ClientSession) -> str:
    try:
        art = Article(url)
        art.config.browser_user_agent = random.choice(USER_AGENTS)
        art.config.request_timeout = 8
        art.config.number_threads = 1

        art.download()
        art.parse()
        txt = (art.text or "").strip()
        if txt and len(txt) > 100:
            return clamp_text(txt)
    except Exception:
        pass

    try:
        await asyncio.sleep(random.uniform(0.1, 0.3))
        
        headers = get_realistic_headers()
        async with session.get(url, headers=headers, timeout=12) as resp:
            if resp.status != 200:
                return ""
                
            html = await resp.text()
            
            if re.search(r"(paywall|subscribe|metered|registration|captcha|access denied)", html, re.I):
                return ""

            extracted = trafilatura.extract(html) or ""
            extracted = extracted.strip()
            if extracted and len(extracted) > 100:
                return clamp_text(extracted)
                
    except Exception:
        pass

    return ""


@router.post("/search-terms")
async def search_terms(payload: Dict[str, str] = Body(...)) -> Dict[str, Any]:
    context = payload.get("context")
    if not context or not isinstance(context, str):
        raise HTTPException(status_code=400, detail="Campo 'context' é obrigatório e deve ser uma string.")
    
    if len(context.strip()) == 0:
        raise HTTPException(status_code=400, detail="Campo 'context' não pode estar vazio.")
    
    # Gera os termos de pesquisa usando o Gemini
    terms = await generate_search_terms(context)
    
    if not terms:
        raise HTTPException(status_code=500, detail="Não foi possível gerar termos de pesquisa válidos.")

    used_urls = set()
    search_semaphore = asyncio.Semaphore(20)
    extract_semaphore = asyncio.Semaphore(50)
    
    async def search_with_limit(client, term):
        async with search_semaphore:
            return await search_brave_term(client, term)
    
    async def process_term(session, term, search_results):
        async with extract_semaphore:
            for result in search_results:
                url = result["url"]
                age = result["age"]
                
                if url in used_urls:
                    continue
                    
                text = await extract_article_text(url, session)
                if text:
                    used_urls.add(url)
                    return {
                        "term": term,
                        "age": age,
                        "url": url,
                        "text": text
                    }
            return None

    connector = aiohttp.TCPConnector(limit=100, limit_per_host=15)
    timeout = aiohttp.ClientTimeout(total=15)
    
    async with httpx.AsyncClient(
        timeout=15.0, 
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=25)
    ) as http_client:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            
            search_tasks = [search_with_limit(http_client, term) for term in terms]
            search_results = await asyncio.gather(*search_tasks, return_exceptions=True)
            
            process_tasks = []
            for term, results in zip(terms, search_results):
                if isinstance(results, list) and results:
                    process_tasks.append(process_term(session, term, results))
            
            if process_tasks:
                processed_results = await asyncio.gather(*process_tasks, return_exceptions=True)
                final_results = [r for r in processed_results if r is not None and not isinstance(r, Exception)]
            else:
                final_results = []

    # Cria o JSON final
    result_data = {"results": final_results}
    
    # Cria arquivo temporário
    temp_file_info = create_temp_file(result_data)
    
    return {
        "message": "Dados salvos em arquivo temporário",
        "total_results": len(final_results),
        "context": context,
        "generated_terms": terms,
        "file_info": temp_file_info
    }


@router.get("/download-temp/{file_id}")
async def download_temp_file(file_id: str):
    """Endpoint para download do arquivo temporário"""
    if file_id not in temp_files:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado ou expirado")
    
    file_info = temp_files[file_id]
    file_path = file_info["path"]
    
    if not file_path.exists():
        temp_files.pop(file_id, None)
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    
    return FileResponse(
        path=str(file_path),
        filename="fontes.txt",
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=fontes.txt"}
    )