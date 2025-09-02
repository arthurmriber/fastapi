import os
import sys
import importlib.util
from pathlib import Path
import re
import json
import time
import logging
import gc
import asyncio
import aiohttp
from typing import Optional, Dict, Any
from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel
from urllib.parse import quote

# IMPORTANTE: Configurar variáveis de ambiente e PyTorch ANTES de qualquer importação que use PyTorch
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"

# Configurar PyTorch ANTES de importar qualquer módulo que o use
import torch
torch.set_num_threads(2)

# Verificar se já foi configurado antes de tentar definir interop threads
if not hasattr(torch, '_interop_threads_set'):
    try:
        torch.set_num_interop_threads(1)
        torch._interop_threads_set = True
    except RuntimeError as e:
        if "cannot set number of interop threads" in str(e):
            print(f"Warning: Could not set interop threads: {e}")
        else:
            raise e

# Supabase Config
SUPABASE_URL = "https://iiwbixdrrhejkthxygak.supabase.co"
SUPABASE_KEY = os.getenv("SUPA_KEY")
SUPABASE_ROLE_KEY = os.getenv("SUPA_SERVICE_KEY")
if not SUPABASE_KEY or not SUPABASE_ROLE_KEY:
    raise ValueError("❌ SUPA_KEY or SUPA_SERVICE_KEY not set in environment!")
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}
SUPABASE_ROLE_HEADERS = {
    "apikey": SUPABASE_ROLE_KEY,
    "Authorization": f"Bearer {SUPABASE_ROLE_KEY}",
    "Content-Type": "application/json"
}

# Rewrite API URL
REWRITE_API_URL = "https://habulaj-newapi-clone3.hf.space/rewrite-news"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("news-analyze-api")

http_session = None

async def get_http_session():
    global http_session
    if http_session is None:
        connector = aiohttp.TCPConnector(
            limit=20,
            limit_per_host=10,
            ttl_dns_cache=300,
            use_dns_cache=True,
            keepalive_timeout=30,
            enable_cleanup_closed=True
        )
        timeout = aiohttp.ClientTimeout(total=30, connect=5)
        http_session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={'User-Agent': 'NewsAnalyzeAPI/1.0 (https://example.com/contact)'}
        )
    return http_session

def load_inference_module():
    """Carrega o módulo inference.py dinamicamente"""
    try:
        # Assumindo que inference.py está no mesmo diretório ou em um caminho conhecido
        inference_path = Path(__file__).parent / "inference.py"  # Ajuste o caminho conforme necessário
        
        if not inference_path.exists():
            # Tenta outros caminhos possíveis
            possible_paths = [
                Path(__file__).parent.parent / "inference.py",
                Path("./inference.py"),
                Path("../inference.py")
            ]
            
            for path in possible_paths:
                if path.exists():
                    inference_path = path
                    break
            else:
                raise FileNotFoundError("inference.py não encontrado")
        
        spec = importlib.util.spec_from_file_location("inference", inference_path)
        inference_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inference_module)
        
        return inference_module
    except Exception as e:
        log.error(f"Erro ao carregar inference.py: {str(e)}")
        return None

# Carrega o módulo na inicialização
inference_module = load_inference_module()

async def rewrite_article_direct(content: str) -> Optional[Dict[str, Any]]:
    """Reescreve o artigo chamando diretamente a função do inference.py"""
    try:
        if not inference_module:
            log.error("Módulo inference não carregado, fallback para API HTTP")
            return await rewrite_article_http(content)
        
        log.info(f"Reescrevendo artigo diretamente: {len(content)} caracteres")
        
        # Cria um objeto similar ao NewsRequest
        class NewsRequest:
            def __init__(self, content: str):
                self.content = content
        
        news_request = NewsRequest(content)
        
        # Chama a função rewrite_news diretamente
        result = await inference_module.rewrite_news(news_request)
        
        # Converte o resultado para dicionário
        rewritten_data = {
            "title": result.title,
            "subhead": result.subhead,
            "content": result.content
        }
        
        # Validação básica da resposta
        required_keys = ["title", "subhead", "content"]
        if all(key in rewritten_data and rewritten_data[key].strip() for key in required_keys):
            log.info("Artigo reescrito com sucesso (chamada direta)")
            return {
                "success": True,
                "data": rewritten_data,
                "raw_response": str(rewritten_data),
                "status_code": 200,
                "method": "direct_call"
            }
        else:
            log.error("Resposta da reescrita direta incompleta")
            return {
                "success": False,
                "error": "Resposta incompleta",
                "data": rewritten_data,
                "raw_response": str(rewritten_data),
                "status_code": 200,
                "method": "direct_call",
                "missing_keys": [key for key in required_keys if not rewritten_data.get(key, "").strip()]
            }
            
    except Exception as e:
        log.error(f"Erro na reescrita direta: {str(e)}")
        log.info("Tentando fallback para API HTTP")
        return await rewrite_article_http(content)

async def rewrite_article_http(content: str) -> Optional[Dict[str, Any]]:
    """Reescreve o artigo usando a API HTTP (função original)"""
    try:
        session = await get_http_session()
        
        payload = {"content": content}
        
        log.info(f"Enviando artigo para reescrita (HTTP): {len(content)} caracteres")
        
        # Timeout maior para a API HTTP
        timeout = aiohttp.ClientTimeout(total=120, connect=10)  # 2 minutos
        
        async with session.post(
            REWRITE_API_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout
        ) as response:
            
            # Log detalhado do status e headers
            log.info(f"Status da resposta HTTP: {response.status}")
            
            # Captura o body completo da resposta
            response_text = await response.text()
            log.info(f"Body completo da resposta HTTP: {response_text}")
            
            if response.status == 200:
                try:
                    # Tenta fazer parse do JSON
                    rewritten_data = json.loads(response_text)
                    
                    # Validação básica da resposta
                    required_keys = ["title", "subhead", "content"]
                    if all(key in rewritten_data for key in required_keys):
                        log.info("Artigo reescrito com sucesso (HTTP)")
                        return {
                            "success": True,
                            "data": rewritten_data,
                            "raw_response": response_text,
                            "status_code": response.status,
                            "method": "http_call"
                        }
                    else:
                        log.error(f"Resposta HTTP incompleta. Chaves encontradas: {list(rewritten_data.keys())}")
                        return {
                            "success": False,
                            "error": "Resposta incompleta",
                            "data": rewritten_data,
                            "raw_response": response_text,
                            "status_code": response.status,
                            "method": "http_call",
                            "missing_keys": [key for key in required_keys if key not in rewritten_data]
                        }
                        
                except json.JSONDecodeError as e:
                    log.error(f"Erro ao fazer parse do JSON: {str(e)}")
                    return {
                        "success": False,
                        "error": f"JSON inválido: {str(e)}",
                        "raw_response": response_text,
                        "status_code": response.status,
                        "method": "http_call"
                    }
            else:
                log.error(f"Erro na API HTTP: {response.status}")
                return {
                    "success": False,
                    "error": f"HTTP {response.status}",
                    "raw_response": response_text,
                    "status_code": response.status,
                    "method": "http_call"
                }
                
    except asyncio.TimeoutError:
        log.error("Timeout na API HTTP")
        return {
            "success": False,
            "error": "Timeout",
            "raw_response": "Timeout occurred",
            "status_code": 0,
            "method": "http_call"
        }
    except Exception as e:
        log.error(f"Erro na API HTTP: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "raw_response": "Exception occurred",
            "status_code": 0,
            "method": "http_call"
        }

async def rewrite_article(content: str) -> Optional[Dict[str, Any]]:
    """Reescreve o artigo - tenta chamada direta primeiro, depois HTTP"""
    
    # Tenta chamada direta primeiro
    result = await rewrite_article_direct(content)
    
    # Se a chamada direta falhou e não foi um fallback, tenta HTTP
    if not result or (not result.get("success") and result.get("method") == "direct_call"):
        log.info("Chamada direta falhou, tentando API HTTP")
        result = await rewrite_article_http(content)
    
    return result

async def fetch_brazil_interest_news():
    """Busca uma notícia com brazil_interest=true e title_pt vazio"""
    try:
        session = await get_http_session()
        url = f"{SUPABASE_URL}/rest/v1/news"
        params = {
            "brazil_interest": "eq.true",
            "title_pt": "is.null",
            "limit": "1",
            "order": "created_at.asc"
        }
        
        async with session.get(url, headers=SUPABASE_HEADERS, params=params) as response:
            if response.status != 200:
                raise HTTPException(status_code=500, detail="Erro ao buscar notícia")
            
            data = await response.json()
            if not data:
                raise HTTPException(status_code=404, detail="Nenhuma notícia com brazil_interest=true e title_pt vazio disponível")
            
            return data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro Supabase: {str(e)}")

async def update_news_rewrite(news_id: int, rewritten_data: Dict[str, str]):
    """Atualiza a notícia com os dados reescritos incluindo campos do Instagram"""
    try:
        session = await get_http_session()
        url = f"{SUPABASE_URL}/rest/v1/news"
        params = {"id": f"eq.{news_id}"}
        
        payload = {
            "title_pt": rewritten_data.get("title", ""),
            "text_pt": rewritten_data.get("content", ""),
            "subhead_pt": rewritten_data.get("subhead", "")
        }
        
        async with session.patch(url, headers=SUPABASE_ROLE_HEADERS, json=payload, params=params) as response:
            if response.status not in [200, 201, 204]:
                response_text = await response.text()
                log.error(f"Erro ao atualizar notícia - Status: {response.status}, Response: {response_text}")
                raise HTTPException(status_code=500, detail=f"Erro ao atualizar notícia - Status: {response.status}")
            
            log.info(f"Notícia {news_id} atualizada com sucesso - Status: {response.status}")
            
    except Exception as e:
        log.error(f"Erro ao atualizar notícia {news_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar: {str(e)}")

def fix_wikipedia_image_url(url: str) -> str:
    if not url or not url.startswith('//upload.wikimedia.org'):
        return url
    
    if url.startswith('//'):
        url = 'https:' + url
    
    url = url.replace('/thumb/', '/')
    parts = url.split('/')
    if len(parts) >= 2:
        filename = parts[-1]
        if 'px-' in filename:
            filename = filename.split('px-', 1)[1]
        base_parts = parts[:-2]
        url = '/'.join(base_parts) + '/' + filename
    
    return url

def extract_birth_death_years(description: str) -> tuple[Optional[int], Optional[int]]:
    if not description:
        return None, None
    
    pattern = r'\((?:born\s+)?(\d{4})(?:[–-](\d{4}))?\)'
    match = re.search(pattern, description)
    
    if match:
        birth_year = int(match.group(1))
        death_year = int(match.group(2)) if match.group(2) else None
        if death_year is None:
            death_year = 2025
        return birth_year, death_year
    
    return None, None

async def fetch_wikipedia_info(entity_name: str) -> Optional[Dict[str, Any]]:
    try:
        session = await get_http_session()
        
        url = f"https://en.wikipedia.org/w/rest.php/v1/search/title"
        params = {'q': entity_name, 'limit': 1}
        
        async with session.get(url, params=params) as response:
            if response.status != 200:
                return None
                
            data = await response.json()
            
            if not data.get('pages'):
                return None
            
            page = data['pages'][0]
            title = page.get('title', '')
            description = page.get('description', '')
            thumbnail = page.get('thumbnail', {})
            
            birth_year, death_year = extract_birth_death_years(description)
            
            image_url = thumbnail.get('url', '') if thumbnail else ''
            if image_url:
                image_url = fix_wikipedia_image_url(image_url)
            
            return {
                'title': title,
                'birth_year': birth_year,
                'death_year': death_year,
                'image_url': image_url
            }
            
    except Exception as e:
        log.error(f"Erro ao buscar Wikipedia: {str(e)}")
        return None

def generate_poster_url(name: str, birth: int, death: int, image_url: str) -> str:
    base_url = "https://habulaj-newapi-clone3.hf.space/cover/memoriam"
    params = f"?image_url={quote(image_url)}&name={quote(name)}&birth={birth}&death={death}"
    return base_url + params

def generate_news_poster_url(image_url: str, headline: str) -> str:
    """Gera URL do poster para notícias normais (não morte)"""
    base_url = "https://habulaj-newapi-clone3.hf.space/cover/news"
    params = f"?image_url={quote(image_url)}&headline={quote(headline)}"
    return base_url + params

async def generate_poster_analysis(news_data: Dict[str, Any], rewritten_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Gera análise de poster baseado nos dados da notícia"""
    try:
        result = {}
        image_url = news_data.get("image", "")
        
        # Verifica se é morte e gera poster apropriado
        if news_data.get("death_related") is True and news_data.get("entity_name"):
            wikipedia_info = await fetch_wikipedia_info(news_data["entity_name"])
            
            if wikipedia_info:
                result["wikipedia_info"] = wikipedia_info
                
                # Gera poster de morte apenas se tiver morte confirmada
                if (wikipedia_info.get("death_year") and 
                    wikipedia_info.get("birth_year")):
                    
                    poster_url = generate_poster_url(
                        wikipedia_info["title"],
                        wikipedia_info["birth_year"],
                        wikipedia_info["death_year"],
                        wikipedia_info.get("image_url", image_url)
                    )
                    result["poster"] = poster_url
        
        # Se não for morte, gera poster de notícia normal
        if "poster" not in result and image_url:
            # Usa headline reescrito se disponível, senão usa título original
            headline_to_use = news_data.get("title_en", "")  # fallback para título original
            if (rewritten_result and 
                rewritten_result.get("success") and 
                rewritten_result.get("data") and 
                rewritten_result["data"].get("title")):
                headline_to_use = rewritten_result["data"]["title"]
            
            news_poster_url = generate_news_poster_url(image_url, headline_to_use)
            result["poster"] = news_poster_url

        return result

    except Exception as e:
        log.error(f"Erro ao gerar poster: {str(e)}")
        return {}

app = FastAPI(title="News Analyze API")
router = APIRouter()

@router.post("/analyze")
async def analyze_endpoint():
    # Busca notícia com brazil_interest=true e title_pt vazio
    news_data = await fetch_brazil_interest_news()
    
    title_en = news_data.get("title_en", "")
    text_en = news_data.get("text_en", "")
    news_id = news_data.get("id")
    
    if not title_en.strip() or not text_en.strip():
        raise HTTPException(status_code=400, detail="Title_en and text_en must not be empty.")
    
    # Executa reescrita (tenta direta primeiro, depois HTTP)
    rewritten_result = await rewrite_article(text_en)
    
    # Log do resultado completo da reescrita
    log.info(f"Resultado completo da reescrita: {json.dumps(rewritten_result, indent=2)}")
    
    # Atualiza no banco de dados se reescrita foi bem-sucedida
    if rewritten_result and rewritten_result.get("success") and rewritten_result.get("data"):
        await update_news_rewrite(news_id, rewritten_result["data"])
    
    # Gera análise de poster
    poster_analysis = await generate_poster_analysis(news_data, rewritten_result)
    
    # Prepara resultado final
    result = {
        "news_id": news_id,
        "title_en": title_en,
        "text_en": text_en,
        "rewrite_result": rewritten_result,
        "death_related": news_data.get("death_related", False),
        "entity_name": news_data.get("entity_name", ""),
        "entity_type": news_data.get("entity_type", ""),
        "image": news_data.get("image", "")
    }
    
    # Adiciona informações do poster se disponíveis
    if poster_analysis:
        result.update(poster_analysis)
    
    return result

app.include_router(router)

@app.on_event("shutdown")
async def shutdown_event():
    global http_session
    if http_session:
        await http_session.close()