# cronjob_router.py
import asyncio
import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter()

# Base URL fixo
BASE_URL = "https://habulaj-newapi-clone3.hf.space"

# Flags para controle das tarefas
tasks = {}
stop_flags = {}

# =======================
# Funções das tarefas
# =======================

async def fetch_news():
    async with httpx.AsyncClient(timeout=10.0, base_url=BASE_URL) as client:
        while not stop_flags.get("news"):
            try:
                response = await client.get("/news")
                if response.status_code == 200:
                    data = response.json()
                    print("[NEWS] Fetched:", data)
                else:
                    print("[NEWS] Erro ao buscar notícias:", response.status_code)
            except Exception as e:
                print("[NEWS] Erro:", e)

            await asyncio.sleep(180)  # 3 minutos

async def fetch_filter():
    async with httpx.AsyncClient(timeout=10.0, base_url=BASE_URL) as client:
        while not stop_flags.get("filter"):
            try:
                response = await client.post("/filter")
                if response.status_code == 200:
                    data = response.json()
                    f = data.get("filter", {})
                    is_news_content = f.get("is_news_content", False)
                    relevance = f.get("relevance", "low").lower()
                    brazil_interest = f.get("brazil_interest", False)

                    print("[FILTER] Fetched:", data)

                    # Repetir imediatamente se critérios não atendidos
                    if not is_news_content or relevance not in ["medium", "high", "viral"] or not brazil_interest:
                        print("[FILTER] Critérios não atendidos, refazendo...")
                        continue
                else:
                    print("[FILTER] Erro ao buscar filter:", response.status_code)
            except Exception as e:
                print("[FILTER] Erro:", e)

            await asyncio.sleep(120)  # 2 minutos

async def fetch_analyze():
    async with httpx.AsyncClient(timeout=10.0, base_url=BASE_URL) as client:
        while not stop_flags.get("analyze"):
            try:
                response = await client.post("/analyze")
                if response.status_code == 200:
                    data = response.json()
                    success = data.get("rewrite_result", {}).get("success", False)
                    print("[ANALYZE] Fetched:", data)

                    if not success:
                        print("[ANALYZE] Success=false, tentando novamente em 1 minuto...")
                        await asyncio.sleep(60)
                        continue
                else:
                    print("[ANALYZE] Erro ao buscar analyze:", response.status_code)
                    await asyncio.sleep(60)
                    continue
            except Exception as e:
                print("[ANALYZE] Erro:", e)
                await asyncio.sleep(60)
                continue

            await asyncio.sleep(180)  # 3 minutos

# =======================
# Endpoints para controle
# =======================

@router.get("/start-cronjob")
async def start_cronjob():
    global tasks, stop_flags

    if tasks:
        raise HTTPException(status_code=400, detail="Cronjob já está rodando!")

    stop_flags = {"news": False, "filter": False, "analyze": False}

    tasks["news"] = asyncio.create_task(fetch_news())
    tasks["filter"] = asyncio.create_task(fetch_filter())
    tasks["analyze"] = asyncio.create_task(fetch_analyze())

    return {"status": "Cronjob iniciado"}

@router.get("/stop-cronjob")
async def stop_cronjob():
    global tasks, stop_flags

    if not tasks:
        raise HTTPException(status_code=400, detail="Nenhum cronjob em execução")

    stop_flags = {k: True for k in stop_flags}

    # Cancela as tarefas
    for t in tasks.values():
        t.cancel()

    tasks = {}
    stop_flags = {}

    return {"status": "Cronjob parado"}