import os
import re
import httpx
from typing import List, Dict
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException

router = APIRouter()

GRAPHQL_URL = "https://api.graphql.imdb.com"
HEADERS = {"Content-Type": "application/json"}

QUERY = """
query GetNews($first: Int!) {
  movieNews: news(first: $first, category: MOVIE) {
    edges {
      node {
        id
        articleTitle { plainText }
        externalUrl
        date
        text { plaidHtml }
        image { url }
      }
    }
  }
  tvNews: news(first: $first, category: TV) {
    edges {
      node {
        id
        articleTitle { plainText }
        externalUrl
        date
        text { plaidHtml }
        image { url }
      }
    }
  }
}
"""

SUPABASE_URL = "https://iiwbixdrrhejkthxygak.supabase.co"
SUPABASE_KEY = os.getenv("SUPA_KEY")
SUPABASE_ROLE_KEY = os.getenv("SUPA_SERVICE_KEY")

if not SUPABASE_KEY or not SUPABASE_ROLE_KEY:
    raise ValueError("SUPA_KEY or SUPA_SERVICE_KEY not set")

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

def clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = BeautifulSoup(raw_html, "html.parser").get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()

@router.get("/news")
async def get_news(first: int = 20) -> List[Dict]:
    payload = {"query": QUERY, "variables": {"first": first}}

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(GRAPHQL_URL, headers=HEADERS, json=payload)
        
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="IMDb API error")

        data = response.json().get("data")
        if not data:
            raise HTTPException(status_code=500, detail="Invalid API response")

        combined = []
        for category_key in ["movieNews", "tvNews"]:
            for edge in data.get(category_key, {}).get("edges", []):
                node = edge.get("node", {})
                image_data = node.get("image")
                combined.append({
                    "news_id": node.get("id"),
                    "title": node.get("articleTitle", {}).get("plainText"),
                    "url": node.get("externalUrl"),
                    "date": node.get("date"),
                    "text": clean_html(node.get("text", {}).get("plaidHtml")),
                    "image": image_data.get("url") if image_data else None,
                    "category": category_key.replace("News", "").upper()
                })

        all_ids = [item["news_id"] for item in combined]
        existing_ids = []
        
        for i in range(0, len(all_ids), 1000):
            chunk = all_ids[i:i + 1000]
            query_ids = ",".join([f"\"{nid}\"" for nid in chunk])
            url = f"{SUPABASE_URL}/rest/v1/news_extraction?select=news_id&news_id=in.({query_ids})"
            r = await client.get(url, headers=SUPABASE_HEADERS)
            if r.status_code == 200:
                existing_ids.extend([item["news_id"] for item in r.json()])

        new_entries = [item for item in combined if item["news_id"] not in existing_ids]

        if new_entries:
            insert_url = f"{SUPABASE_URL}/rest/v1/news_extraction"
            await client.post(insert_url, headers=SUPABASE_ROLE_HEADERS, json=new_entries)

        return sorted(combined, key=lambda x: x.get("date"), reverse=True)
