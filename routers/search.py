from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
import httpx
import os
import re
import struct
from typing import Optional, Tuple, List, Dict
from functools import lru_cache
import asyncio
import time

router = APIRouter()

# Cache otimizado
_cache = {}
_cache_limit = 300

@lru_cache(maxsize=100)
def clean_url(url: str) -> str:
    if 'wikimedia.org' in url and '/thumb/' in url:
        try:
            parts = url.split('/thumb/')
            if len(parts) == 2:
                after = parts[1].split('/')
                if len(after) >= 3:
                    return f"{parts[0]}/{'/'.join(after[:3])}"
        except:
            pass
    return url

def extract_urls(text: str) -> List[Dict]:
    pattern = re.compile(r'https?://[^\s"\'<>]+?\.(?:jpg|png|webp|jpeg)\b', re.IGNORECASE)
    image_urls = pattern.findall(text)
    
    seen_urls = set()
    images = []
    
    for url in image_urls[:150]:
        cleaned_url = clean_url(url)
        if cleaned_url not in seen_urls:
            seen_urls.add(cleaned_url)
            images.append({"url": cleaned_url, "width": None, "height": None})
    
    return images

def get_size_fast(data: bytes) -> Optional[Tuple[int, int]]:
    if len(data) < 24:
        return None
    
    try:
        # JPEG - parsing otimizado
        if data[:2] == b'\xff\xd8':
            for i in range(2, min(len(data) - 8, 600)):
                if data[i:i+2] in (b'\xff\xc0', b'\xff\xc2'):
                    if i + 9 <= len(data):
                        h = struct.unpack('>H', data[i+5:i+7])[0]
                        w = struct.unpack('>H', data[i+7:i+9])[0]
                        if w > 0 and h > 0:
                            return w, h
        
        # PNG
        elif data[:8] == b'\x89PNG\r\n\x1a\n' and len(data) >= 24:
            w = struct.unpack('>I', data[16:20])[0]
            h = struct.unpack('>I', data[20:24])[0]
            if w > 0 and h > 0:
                return w, h
        
        # WebP
        elif data[:12] == b'RIFF' + data[4:8] + b'WEBP' and len(data) >= 30:
            if data[12:16] == b'VP8 ':
                w = struct.unpack('<H', data[26:28])[0] & 0x3fff
                h = struct.unpack('<H', data[28:30])[0] & 0x3fff
                if w > 0 and h > 0:
                    return w, h
    except:
        pass
    return None

async def process_image_fast(client: httpx.AsyncClient, url: str) -> Dict:
    cache_key = url
    if cache_key in _cache:
        return _cache[cache_key].copy()
    
    clean_img_url = url.replace('\\u003d', '=').replace('\\u0026', '&').replace('\\\\', '').replace('\\/', '/')
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'image/*',
        'Connection': 'close'
    }
    
    w, h = None, None
    
    try:
        # Apenas range pequeno para dimensões
        headers['Range'] = 'bytes=0-2048'
        
        try:
            r = await client.get(clean_img_url, headers=headers, timeout=4.0)
            if r.status_code in [200, 206] and len(r.content) > 50:
                size = get_size_fast(r.content)
                if size:
                    w, h = size
        except:
            pass
        
        # Fallback sem range se necessário
        if not w or not h:
            try:
                del headers['Range']
                r = await client.get(clean_img_url, headers=headers, timeout=5.0)
                if r.status_code == 200 and len(r.content) < 500000:  # Max 500KB para dimensões
                    # Primeiro tenta parsing rápido
                    size = get_size_fast(r.content)
                    if size:
                        w, h = size
                    else:
                        # Fallback PIL apenas se necessário
                        try:
                            from PIL import Image
                            import io
                            with Image.open(io.BytesIO(r.content)) as img:
                                w, h = img.size
                        except:
                            pass
            except:
                pass
    except:
        pass
    
    result = {"url": clean_img_url, "width": w, "height": h}
    
    if len(_cache) < _cache_limit:
        _cache[cache_key] = result.copy()
    
    return result

async def process_batch_cpu_optimized(images: List[Dict]) -> List[Dict]:
    if not images:
        return []
    
    # Configuração para CPU otimizada
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(8.0),
        limits=httpx.Limits(
            max_keepalive_connections=30,  # Menos conexões para menos overhead
            max_connections=40,
            keepalive_expiry=20.0
        ),
        http2=False
    )
    
    # Semáforo reduzido para menos overhead de context switching
    sem = asyncio.Semaphore(15)
    
    async def process_one(img_data):
        async with sem:
            return await process_image_fast(client, img_data["url"])
    
    try:
        tasks = [process_one(img) for img in images]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if not isinstance(r, Exception) and r.get('width') and r.get('height')]
    finally:
        await client.aclose()

@router.get("/search")
async def search(
    q: str = Query(...),
    min_width: int = Query(1200)
):
    start = time.time()
    
    google_images_url = "http://www.google.com/search"
    
    params = {
        "tbm": "isch",
        "q": q,
        "start": 0,
        "sa": "N",
        "asearch": "arc",
        "cs": "1",
        "tbs": "isz:l",
        "async": "arc_id:srp_GgSMaOPQOtL_5OUPvbSTOQ_110,ffilt:all,ve_name:MoreResultsContainer,inf:1,_id:arc-srp_GgSMaOPQOtL_5OUPvbSTOQ_110,_pms:s,_fmt:pc"
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.8,en-US;q=0.5,en;q=0.3",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Referer": "https://www.google.com/"
    }
    
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            r = await client.get(google_images_url, params=params, headers=headers)
        
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail="Google search failed")
        
        print(f"Google: {time.time() - start:.2f}s")
        
        images = extract_urls(r.text)
        print(f"URLs: {len(images)}")
        
        results = await process_batch_cpu_optimized(images)
        print(f"Processed: {len(results)}")
        
        valid = [img for img in results if img.get('width', 0) >= min_width]
        
        # Segunda busca se necessário
        if len(valid) < 15:
            params["tbs"] = "isz:lt,islt:4mp"
            async with httpx.AsyncClient(timeout=20.0) as client:
                r2 = await client.get(google_images_url, params=params, headers=headers)
            
            if r2.status_code == 200:
                more_images = extract_urls(r2.text)
                more_results = await process_batch_cpu_optimized(more_images)
                
                seen_urls = {img.get('url') for img in valid}
                for img in more_results:
                    if (img.get('url') not in seen_urls 
                        and img.get('width', 0) >= min_width 
                        and img.get('height', 0) > 0):
                        valid.append(img)
                        seen_urls.add(img.get('url'))
        
        valid.sort(key=lambda x: x.get('width', 0), reverse=True)
        final = valid[:40]
        
        total_time = time.time() - start
        print(f"TOTAL: {total_time:.2f}s - {len(final)} images")
        
        return JSONResponse(content={
            "query": q,
            "min_width_filter": min_width,
            "total_found": len(final),
            "processing_time": round(total_time, 2),
            "images": final
        })
        
    except httpx.TimeoutException:
        raise HTTPException(status_code=408, detail="Timeout")
    except Exception as e:
        print(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
