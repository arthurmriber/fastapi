import os
import re
import json
import asyncio
import httpx
import random
from typing import Dict, Any
from fastapi import FastAPI, APIRouter, HTTPException
from google import genai
from google.genai import types
from newspaper import Article
import trafilatura

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

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0'
]

def get_realistic_headers():
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive'
    }

async def extract_article_text(url: str) -> str:
    try:
        # Method 1: newspaper3k
        try:
            article = Article(url)
            article.config.browser_user_agent = random.choice(USER_AGENTS)
            article.config.request_timeout = 10
            article.download()
            article.parse()
            
            if article.text and len(article.text.strip()) > 100:
                return article.text.strip()
        except:
            pass
        
        # Method 2: trafilatura fallback
        async with httpx.AsyncClient(timeout=30.0) as client:
            await asyncio.sleep(random.uniform(1, 2))
            headers = get_realistic_headers()
            
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                extracted_text = trafilatura.extract(response.text)
                if extracted_text and len(extracted_text.strip()) > 100:
                    return extracted_text.strip()
        
        return ""
    except:
        return ""

async def fetch_unused_news():
    async with httpx.AsyncClient() as client:
        params = {"used": "eq.false", "limit": "1", "order": "created_at.asc"}
        response = await client.get(
            f"{SUPABASE_URL}/rest/v1/news_extraction",
            headers=SUPABASE_HEADERS,
            params=params
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Erro ao buscar notícia")
        
        data = response.json()
        if not data:
            raise HTTPException(status_code=404, detail="Nenhuma notícia disponível")
        
        return data[0]

async def fetch_last_50_titles():
    try:
        async with httpx.AsyncClient() as client:
            params = {"select": "title_pt", "limit": "50", "order": "created_at.desc"}
            response = await client.get(
                f"{SUPABASE_URL}/rest/v1/news",
                headers=SUPABASE_HEADERS,
                params=params
            )
            
            if response.status_code != 200:
                return []
            
            data = response.json()
            return [item.get("title_pt", "") for item in data if item.get("title_pt")]
    except:
        return []

async def insert_news_to_db(title: str, text: str, news_id: str, url: str, image_url: str, filters: dict):
    payload = {
        "title_en": title,
        "text_en": text,
        "news_id": news_id,
        "url": url,
        "image": image_url,
        **filters
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{SUPABASE_URL}/rest/v1/news",
            headers=SUPABASE_ROLE_HEADERS,
            json=payload
        )
        
        if response.status_code not in [200, 201]:
            raise HTTPException(status_code=500, detail=f"Erro ao inserir notícia")

async def mark_news_as_used(news_id: str):
    try:
        async with httpx.AsyncClient() as client:
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/news_extraction",
                headers=SUPABASE_ROLE_HEADERS,
                json={"used": True},
                params={"news_id": f"eq.{news_id}"}
            )
    except:
        pass

def extract_json(text):
    match = re.search(r'\{.*\}', text, flags=re.DOTALL)
    return match.group(0) if match else text

async def filter_news(title: str, content: str, last_titles: list) -> dict:
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        model = "gemini-2.5-flash-lite"

        SYSTEM_INSTRUCTIONS = """
        Analyze the news title and content, and return the filters in JSON format with the defined fields.
Please respond ONLY with the JSON filter, do NOT add any explanations, system messages, or extra text.

death_related (true | false): Whether the news involves the real-life death of a person. Does not include fictional character deaths or deaths within stories.
political_related (true | false): Related to real-world politics (governments, elections, politicians, or official decisions). Not about political storylines in fiction.
woke_related (true | false): Involves social issues like inclusion, diversity, racism, gender, LGBTQIA+, etc.
spoilers (true | false): Reveals important plot points (e.g., character deaths, endings, major twists).
sensitive_theme (true | false): Covers sensitive or disturbing topics like suicide, abuse, violence, or tragedy.
contains_video (true | false): The news includes an embedded video (e.g., trailer, teaser, interview, video report).
is_news_content (true | false): Whether the content is actual news reporting. True for breaking news, announcements, factual reports. False for reviews, opinion pieces, lists, rankings, recommendations, critiques, analysis, or editorial content.
relevance ("low" | "medium" | "high" | "viral"): The expected public interest or impact of the news.
brazil_interest (true | false): True only if the news topic has a clear and direct impact, relevance, or interest for the Brazilian audience. This includes:

Events, releases, or announcements happening in Brazil or significant international announcements.
Content (movies, series, sports, games, music) officially available in Brazil.
People, teams, companies, brands, or productions that are relevant and recognized by the Brazilian audience.
International celebrities, athletes, or artists with significant fan bases in Brazil.

Do not mark as true if the content is unknown to most of the Brazilian population or if the actors, artists, or productions do not have notable recognition in the country.

Examples:

"Couple on 'House Hunters' with a 30-year age difference shocks viewers" — TRUE (In Brazil, House Hunters is Em Busca da Casa Perfeita, so it is available)
"Wild Bill Wichrowski from 'Deadliest Catch' will miss the 21st season after battling prostate cancer" — TRUE (Because Deadliest Catch is known in Brazil as Pesca Mortal)
"Loni Anderson, star of 'WKRP in Cincinnati,' dies at 79" — FALSE (Few people know her in Brazil, and WKRP in Cincinnati is not available there)
"The 'forgotten' film in the 'Conjuring' universe: why 'The Curse of La Llorona' is considered the worst of the franchise" — TRUE
"Rose Byrne collapses: new A24 film described as a 'test of endurance'" — TRUE (Rose Byrne is well-known in Brazil)
"Star Trek: how to understand the timeline of one of the greatest sci-fi sagas" — TRUE
"Crisis at Mubi: top filmmakers, including Israelis, demand boycott over ties to military investor" — TRUE (Mubi operates in Brazil)
"Liam Neeson and Joe Keery face biological terror in the trailer for Cold Storage" — TRUE (Joe Keery is well-known in Brazil for Stranger Things)
"TIFF 2025: from John Candy to Lucrecia Martel, meet the documentaries of the year" — TRUE (Toronto International Film Festival is one of the most famous independent festivals, so it is considered relevant to Brazil)
"TIFF 2025: festival announces documentaries with Lucrecia Martel and a production by Barack and Michelle Obama" — TRUE (Toronto International Film Festival is well-known, relevant to Brazil)
"'Stranger Things' universe expands: animated series and stage play confirmed" — TRUE (Stranger Things is well-known in Brazil)
"New Park Chan-wook film with stars from 'Squid Game' and 'Landing on Love' will open a film festival" — TRUE (No Other Choice features a famous actor from Squid Game)
"Francis Ford Coppola hospitalized in Rome, but reassures fans: 'I'm fine'" — TRUE (Francis Coppola is internationally known)
"Ken Jennings used 'Who Wants to Be a Millionaire?' to provoke a rival, but the scene was cut" — FALSE (This program is not Brazilian; Brazil has its own more popular version)
"Canelo vs. Crawford: Netflix confirms fight of the century without pay-per-view cost" — TRUE (Even though they are not Brazilian, fights usually attract worldwide interest)

breaking_news (true | false): The content is urgent or part of a recent and unfolding event.
audience_age_rating ("L" | 10 | 12 | 14 | 16 | 18): Content rating based on Brazilian standards.
regional_focus ("global" | "americas" | "europe" | "asia" | "africa" | "middle_east" | "oceania"): The main geographic region the news relates to.
country_focus (ISO 3166-1 alpha-2 code like "br", "us", "fr", "jp" or null): The specific country the news is about, if applicable.
ideological_alignment ("left" | "center-left" | "center" | "center-right" | "right" | "apolitical"): The perceived political bias of the article.
entity_type ("movie" | "series" | "event" | "person" | "place" | "other"): The type of main subject mentioned in the news.
entity_name (string): The name of the person, title, event, or topic the article is primarily about.
duplication (true | false): Whether the current news is a duplicate or highly similar to any of the previously published news titles (Last titles).
        """

        last_titles_formatted = "\n- ".join(last_titles[:25]) if last_titles else "No previous titles available"

        # Primeiro exemplo - SÉRIE HBO RENOVADA
        EXAMPLE_INPUT_1 = f"""Title: 'The Gilded Age' Renewed for Season 4 at HBO — Everything We Know So Far
Content: The Gilded Age will return. HBO announced on Monday, July 28, that the series has been renewed for Season 4. This comes after the release of Season 3 Episode 6 on Sunday, July 27. There are two episodes left to go in the third season. The Season 3 finale will air on Sunday, August 10, on HBO. According to HBO, total premiere-night viewing for the third season has grown for five consecutive weeks, culminating in a 20 percent growth compared to last season. Fan engagement has also climbed, with social chatter rising nearly 60 percent week over week. The show has also received its most critical acclaim to date with Season 3, its highest-stakes season so far. In the July 27 episode, the series that's known for its low stakes but high-camp drama, a character was seemingly killed off in violent (for The Gilded Age) fashion. The show is already Emmy-winning. Production designer Bob Shaw took home an Emmy for
Last titles:
- 'Quarteto Fantástico: Primeiros Passos' dispara para arrecadar US$ 118 milhões nas bilheterias dos EUA e US$ 218 milhões globalmente
- Bilheteria: 'Quarteto Fantástico: Primeiros Passos' sobe para US$ 218 milhões globalmente, 'Superman' e 'F1' ultrapassam US$ 500 milhões
- Reboot de 'Quarteto Fantástico' da Marvel ultrapassa US$ 200 milhões globalmente"""

        EXAMPLE_OUTPUT_1 = """{
   "death_related":false,
   "political_related":false,
   "woke_related":false,
   "spoilers":false,
   "sensitive_theme":false,
   "contains_video":false,
   "is_news_content":true,
   "relevance":"low",
   "brazil_interest":true,
   "breaking_news":true,
   "audience_age_rating":14,
   "regional_focus":"americas",
   "country_focus":"us",
   "ideological_alignment":"apolitical",
   "entity_type":"series",
   "entity_name":"The Gilded Age",
   "duplication":false
}"""

        # Segundo exemplo - SEQUÊNCIA DE FILME
        EXAMPLE_INPUT_2 = f"""Title: 'My Best Friend's Wedding' Sequel in the Works: 'Materialists,' 'Past Lives' Director Celine Song to Write Screenplay
Content: A sequel to the Julia Roberts romantic comedy "My Best Friend's Wedding" is in early development at Sony Pictures. The studio has tapped "Materialists" and "Past Lives" writer-director Celine Song to pen a screenplay for the project, though she is not in talks to helm the feature. 
Last titles:
- Sequência de "The Batman" ganha data de lançamento oficial da Warner Bros
- Sequência de "The Batman" de Robert Pattinson tem data oficial de lançamento para 2026
- Warner Bros. define data de lançamento da sequência de "The Batman" para 2026
- Sequência de 'O Casamento do Meu Melhor Amigo' terá roteiro da diretora de 'Vidas Passadas'"""

        EXAMPLE_OUTPUT_2 = """{
   "death_related":false,
   "political_related":false,
   "woke_related":false,
   "spoilers":false,
   "sensitive_theme":false,
   "contains_video":false,
   "is_news_content":true,
   "relevance":"medium",
   "brazil_interest":true,
   "breaking_news":false,
   "audience_age_rating":10,
   "regional_focus":"americas",
   "country_focus":"us",
   "ideological_alignment":"apolitical",
   "entity_type":"movie",
   "entity_name":"My Best Friend's Wedding",
   "duplication":true
}"""

        # Terceiro exemplo - SÉRIE COM SPOILERS E MORTE DE PERSONAGEM
        EXAMPLE_INPUT_3 = f"""Title: 9-1-1: Death of main character shakes series, which gets new date for the 9th season
Content: The 9-1-1 universe was permanently redefined after one of the most shocking events in its history. The show's eighth season bid farewell to one of its pillars with the death of Captain Bobby Nash, played by Peter Krause, in episode 15. Now, with the renewal for a ninth season confirmed, ABC has announced a schedule change: the premiere has been moved up to Thursday, October 9, 2025. Bobby Nash's death, the first of a main cast member, leaves a leadership vacuum in Battalion 118 and sets the main narrative arc for the new episodes. Peter Krause's departure had already been signaled, but the impact of his absence will be the driving force behind the next season, which will have 18 episodes. Showrunner Tim Minear had previously stated that, despite the death, the character would still appear in specific moments in the eighth season finale, fulfilling his promise.
Last titles:
- The Batman 2 ganha data oficial de lançamento para 2026 na Warner Bros
- Datas de estreia da ABC no outono de 2025: '9-1-1', 'Nashville' e 'Grey's Anatomy' antecipadas
- Warner Bros. anuncia sequência de 'The Batman' para 2026"""

        EXAMPLE_OUTPUT_3 = """{
   "death_related":false,
   "political_related":false,
   "woke_related":false,
   "spoilers":true,
   "sensitive_theme":false,
   "contains_video":false,
   "is_news_content":true,
   "relevance":"high",
   "brazil_interest":true,
   "breaking_news":true,
   "audience_age_rating":14,
   "regional_focus":"global",
   "country_focus":null,
   "ideological_alignment":"apolitical",
   "entity_type":"series",
   "entity_name":"9-1-1",
   "duplication":true
}"""

        # Quarto exemplo - MORTE DE CELEBRIDADE
        EXAMPLE_INPUT_4 = f"""Title: Julian McMahon, 'Fantastic Four,' 'Nip/Tuck' and 'FBI: Most Wanted' Star, Dies at 56
Content: Julian McMahon, the suave Australian actor best known for his performances on "FBI: Most Wanted," "Charmed," "Nip/Tuck" and the early aughts "Fantastic Four" films, died Wednesday in Florida. He was 56 and died after a battle with cancer. McMahon's death was confirmed through his reps, who shared a statement from his wife, Kelly McMahon, in remembrance of her husband. "With an open heart, I wish to share with the world that my beloved husband, Julian McMahon, died peacefully this week after a valiant effort to overcome cancer," she said. "Julian loved life. He loved his family. He loved his friends. He loved his work, and he loved his fans. His deepest wish was to bring joy into as many lives as possible. We ask for support during this time to allow our family to grieve in privacy. And we wish for all of those to whom Julian brought joy, to continue to find joy in life. We are grateful for the memories."
Last titles:
- Mortes de Celebridades em 2025: Estrelas que Perdemos Este Ano
- Programas de TV Cancelados em 2025: Quais Séries Foram Canceladas
- Atores Australianos que Estão Fazendo Sucesso em Hollywood"""

        EXAMPLE_OUTPUT_4 = """{
   "death_related":true,
   "political_related":false,
   "woke_related":false,
   "spoilers":false,
   "sensitive_theme":true,
   "contains_video":false,
   "is_news_content":true,
   "relevance":"high",
   "brazil_interest":true,
   "breaking_news":true,
   "audience_age_rating":14,
   "regional_focus":"americas",
   "country_focus":"au",
   "ideological_alignment":"apolitical",
   "entity_type":"person",
   "entity_name":"Julian McMahon",
   "duplication":false
}"""

        # Quinto exemplo - SEQUÊNCIA DE FILME COM ELEMENTOS POLÍTICOS
        EXAMPLE_INPUT_5 = f"""Title: Mikey Madison and Jeremy Allen White Circling Lead Roles in Aaron Sorkin's 'Social Network' Sequel
Content: Mikey Madison and Jeremy Allen White are circling the lead roles for Aaron Sorkin's sequel to the 2010 Oscar winner "The Social Network," according to sources with knowledge of the project. While no offers have been made, Sorkin has met with both Madison and White about the project. The film is still very much in the development stage and has yet to receive the green light from Sony.
Last titles:
- Wild Bill Wichrowski do 'Deadliest Catch' ficará de fora da 21ª temporada após batalha contra o câncer de próstata
- Loni Anderson, estrela de 'WKRP in Cincinnati', morre aos 79 anos
- O filme "esquecido" do universo "Invocação do Mal": entenda por que "A Maldição da Chorona" é considerado o pior da franquia
- Rose Byrne em colapso: novo filme da A24 é descrito como 'teste de resistência'
- Jornada nas Estrelas: como entender a linha do tempo de uma das maiores sagas da ficção
- Crise na Mubi: cineastas de peso, incluindo israelenses, exigem boicote por laços com investidor militar"""

        EXAMPLE_OUTPUT_5 = """{
   "death_related":false,
   "political_related":true,
   "woke_related":false,
   "spoilers":false,
   "sensitive_theme":false,
   "contains_video":false,
   "is_news_content":true,
   "relevance":"high",
   "brazil_interest":true,
   "breaking_news":true,
   "audience_age_rating":14,
   "regional_focus":"americas",
   "country_focus":"au",
   "ideological_alignment":"apolitical",
   "entity_type":"movie",
   "entity_name":"The Social Network",
   "duplication":false
}"""

        # Sexto exemplo - EPISÓDIO COM SPOILERS
        EXAMPLE_INPUT_6 = f"""Title: Star Trek: Strange New Worlds' Holodeck Episode Began As A Tribute To A DS9 Masterpiece [Exclusive]
Content: Spoilers for episode 4 of "Star Trek: Strange New Worlds" season 4, titled "A Space Adventure Hour," episode follow. The newest episode of "Star Trek: Strange New Worlds" — "A Space Adventure Hour," written by Dana Horgan & Kathryn Lyn — features the show going back to the past. Except, it's not a time travel episode. To test a prototype holodeck, La'an (Christina Chong) crafts a murder mystery story set in mid-20th century Hollywood where she's the detective, Amelia Moon. And the suspects are the cast and crew of a space adventure series, "The Last Frontier," that's about to be canceled. The episode has enough metatext to fill the whole Enterprise, because "The Last Frontier" is a clear stand-in for "Star Trek: The Original Series." However, the writers weren't just thinking about "TOS" when it came to "A Space Adventure Hour."
Last titles:
- Wild Bill Wichrowski do 'Deadliest Catch' ficará de fora da 21ª temporada após batalha contra o câncer de próstata
- Loni Anderson, estrela de 'WKRP in Cincinnati', morre aos 79 anos
- O filme "esquecido" do universo "Invocação do Mal": entenda por que "A Maldição da Chorona" é considerado o pior da franquia
- Rose Byrne em colapso: novo filme da A24 é descrito como 'teste de resistência'
- Jornada nas Estrelas: como entender a linha do tempo de uma das maiores sagas da ficção
- Crise na Mubi: cineastas de peso, incluindo israelenses, exigem boicote por laços com investidor militar"""

        EXAMPLE_OUTPUT_6 = """{
   "death_related": false,
   "political_related": false,
   "woke_related": false,
   "spoilers": true,
   "sensitive_theme": false,
   "contains_video": false,
   "is_news_content": true,
   "relevance": "medium",
   "brazil_interest": true,
   "breaking_news": false,
   "audience_age_rating": 10,
   "regional_focus": "global",
   "country_focus": "us",
   "ideological_alignment": "apolitical",
   "entity_type": "series",
   "entity_name": "Star Trek: Strange New Worlds",
   "duplication": false
}"""

        # Sétimo exemplo - SÉRIE DE HORROR (TEMA SENSÍVEL)
        EXAMPLE_INPUT_7 = f"""Title: 'Hostel' TV Series From Eli Roth and Starring Paul Giamatti Lands at Peacock for Development (Exclusive)
Content: The "Hostel" TV series has found a home at Peacock. Variety has learned exclusively that the TV extension of the horror film franchise is currently in development at the NBCUniversal streamer. The show was previously reported to be in the works in June 2024, but no platform was attached at that time. As originally reported, Paul Giamatti is attached to star in the series, with "Hostel" mastermind Eli Roth set to write, direct, and executive produce. Chris Briggs and Mike Fleiss, who have produced all the "Hostel" films, are also executive producers. Fifth Season is the studio. Exact plot details are being kept under wraps.
Last titles:
- Wild Bill Wichrowski do 'Deadliest Catch' ficará de fora da 21ª temporada após batalha contra o câncer de próstata
- Loni Anderson, estrela de 'WKRP in Cincinnati', morre aos 79 anos
- O filme "esquecido" do universo "Invocação do Mal": entenda por que "A Maldição da Chorona" é considerado o pior da franquia
- Rose Byrne em colapso: novo filme da A24 é descrito como 'teste de resistência'
- Jornada nas Estrelas: como entender a linha do tempo de uma das maiores sagas da ficção
- Crise na Mubi: cineastas de peso, incluindo israelenses, exigem boicote por laços com investidor militar"""

        EXAMPLE_OUTPUT_7 = """{
   "death_related": false,
   "political_related": false,
   "woke_related": false,
   "spoilers": false,
   "sensitive_theme": true,
   "contains_video": false,
   "is_news_content": true,
   "relevance": "medium",
   "brazil_interest": false,
   "breaking_news": false,
   "audience_age_rating": 18,
   "regional_focus": "global",
   "country_focus": "us",
   "ideological_alignment": "apolitical",
   "entity_type": "series",
   "entity_name": "Hostel",
   "duplication": false
}"""

        # Oitavo exemplo - EVENTO ESPORTIVO
        EXAMPLE_INPUT_8 = f"""Title: Is Canelo vs. Crawford Free on Netflix? Here's How to Watch the Fight 
Content: When boxing legends Saúl "Canelo" Álvarez and Terence "Bud" Crawford meet in the ring on Sept. 13, it won't just be a clash of champions — it could be a career-defining moment. For the first time ever two of the most dominant fighters of their generation will share the ring. Only one will walk away as the greatest of their era. Given the high stakes and the long tradition of pay-per-view boxing events, fans are asking: Is Canelo vs. Crawford free on Netflix? Keep scrolling to learn more.
Last titles:
- Wild Bill Wichrowski do 'Deadliest Catch' ficará de fora da 21ª temporada após batalha contra o câncer de próstata
- Loni Anderson, estrela de 'WKRP in Cincinnati', morre aos 79 anos
- O filme "esquecido" do universo "Invocação do Mal": entenda por que "A Maldição da Chorona" é considerado o pior da franquia
- Rose Byrne em colapso: novo filme da A24 é descrito como 'teste de resistência'
- Jornada nas Estrelas: como entender a linha do tempo de uma das maiores sagas da ficção
- Crise na Mubi: cineastas de peso, incluindo israelenses, exigem boicote por laços com investidor militar"""

        EXAMPLE_OUTPUT_8 = """{
   "death_related": false,
   "political_related": false,
   "woke_related": false,
   "spoilers": false,
   "sensitive_theme": false,
   "contains_video": false,
   "is_news_content": true,
   "relevance": "high",
   "brazil_interest": true,
   "breaking_news": false,
   "audience_age_rating": 10,
   "regional_focus": "global",
   "country_focus": "us",
   "ideological_alignment": "apolitical",
   "entity_type": "event",
   "entity_name": "Canelo Álvarez vs. Terence Crawford",
   "duplication": false
}"""

        # Nono exemplo - MORTE DE CELEBRIDADE (DUPLICAÇÃO)
        EXAMPLE_INPUT_9 = f"""Title: Loni Anderson, Emmy- and Golden Globe-Nominated Star of 'Wkrp in Cincinnati,' Dies at 79
Content: Loni Anderson, whose beloved role as Jennifer Marlowe on "WKRP in Cincinnati" was nominated for Emmy and Golden Globe awards, has died, her publicist confirmed Sunday. She was 79.
Last titles:
- Wild Bill Wichrowski do 'Deadliest Catch' ficará de fora da 21ª temporada após batalha contra o câncer de próstata
- Loni Anderson, estrela de 'WKRP in Cincinnati', morre aos 79 anos
- O filme "esquecido" do universo "Invocação do Mal": entenda por que "A Maldição da Chorona" é considerado o pior da franquia
- Rose Byrne em colapso: novo filme da A24 é descrito como 'teste de resistência'
- Jornada nas Estrelas: como entender a linha do tempo de uma das maiores sagas da ficção
- Crise na Mubi: cineastas de peso, incluindo israelenses, exigem boicote por laços com investidor militar
- Liam Neeson e Joe Keery enfrentam terror biológico no trailer de Cold Storage
- TIFF 2025: de John Candy a Lucrecia Martel, conheça os documentários do ano"""

        EXAMPLE_OUTPUT_9 = """{
   "death_related": true,
   "political_related": false,
   "woke_related": false,
   "spoilers": false,
   "sensitive_theme": false,
   "contains_video": false,
   "is_news_content": true,
   "relevance": "medium",
   "brazil_interest": false,
   "breaking_news": true,
   "audience_age_rating": 10,
   "regional_focus": "global",
   "country_focus": "us",
   "ideological_alignment": "apolitical",
   "entity_type": "person",
   "entity_name": "Loni Anderson",
   "duplication": true
}"""

        # Décimo exemplo - FILME DE FESTIVAL (BAIXA RELEVÂNCIA)
        EXAMPLE_INPUT_10 = f"""Title: Jim Jarmusch's 'Father Mother Sister Brother' Sells to Multiple Territories Ahead of Venice Premiere
Content: Jim Jarmusch's "Father Mother Sister Brother" has sold to multiple territories ahead of its world premiere in competition at the Venice Film Festival. The film stars Tom Waits, Adam Driver, Mayim Bialik, Charlotte Rampling, Cate Blanchett, Vicky Krieps, Sarah Greene, Indya Moore, Luka Sabbat and Françoise Lebrun. Distribution rights have been picked up in Italy (Lucky Red), Spain (Avalon Distribucion Audiovisual), Portugal (Nos Lusomundo), Greece (Cinobo), Poland (Gutek Film), Hungary (Cirko Films), Romania (Bad Unicorn), Former Yugoslavia (MCF MegaCom Film), Czech Republic and Slovakia (Aerofilms), Middle East and North Africa (Front Row Filmed Ent.), South Korea (Andamiro Films), and Hong Kong (Edko Films).
Last titles:
- Wild Bill Wichrowski do 'Deadliest Catch' ficará de fora da 21ª temporada após batalha contra o câncer de próstata
- Loni Anderson, estrela de 'WKRP in Cincinnati', morre aos 79 anos
- O filme "esquecido" do universo "Invocação do Mal": entenda por que "A Maldição da Chorona" é considerado o pior da franquia
- Rose Byrne em colapso: novo filme da A24 é descrito como 'teste de resistência'
- Jornada nas Estrelas: como entender a linha do tempo de uma das maiores sagas da ficção
- Crise na Mubi: cineastas de peso, incluindo israelenses, exigem boicote por laços com investidor militar
- Universo 'Stranger Things' se expande: série animada e peça de teatro são confirmadas
- Wandinha: O que já sabemos sobre a 2ª temporada e os boatos que circulam na internet
- Novo filme de Park Chan-wook, 'No Other Choice', escala festivais e une estrelas
- Homem-Aranha 4: Tom Holland revela novo traje e produção de 'Um Novo Dia' começa com participações surpreendentes
- Quarteto Fantástico segue no topo das bilheterias, mas queda preocupa
- Novo filme de Jim Jarmusch com Adam Driver e Cate Blanchett será distribuído pela MUBI
- Tulsa King: 3ª temporada com Sylvester Stallone ganha data de estreia e primeiras imagens"""

        EXAMPLE_OUTPUT_10 = """{
   "death_related": false,
   "political_related": false,
   "woke_related": false,
   "spoilers": false,
   "sensitive_theme": false,
   "contains_video": false,
   "is_news_content": true,
   "relevance": "low",
   "brazil_interest": false,
   "breaking_news": false,
   "audience_age_rating": 10,
   "regional_focus": "global",
   "country_focus": "us",
   "ideological_alignment": "apolitical",
   "entity_type": "movie",
   "entity_name": "Father Mother Sister Brother",
   "duplication": true
}"""

        EXAMPLE_INPUT_11 = f"""Title: ‘AGT’: Husband & Wife Comedians Audition Against Each Other — Did Either Make the Live Shows?
Content: Press The Golden Buzzer! For exclusive news and updates, subscribe to our America's Got Talent Newsletter:\n\nAmerica’s Got Talent has seen several couples audition together over the years, but it’s rare to see a husband and wife competing against one another. But that’s exactly what happened on Tuesday’s (August 5) episode.\n\nComedian Matt O’Brien and his wife, Julia Hladkowicz, also a comic, both auditioned for the NBC competition series separately. O’Brien was up first, winning the judges over with his jokes about being married versus being single.\n\n“You are really, really good,” Howie Mandel told the Canadian comic. “You deserve to be here. You’re the kind of comedian that could go really far in this, so I want to be the first one to give you a yes.”
Last titles:
- Wild Bill Wichrowski do 'Deadliest Catch' ficará de fora da 21ª temporada após batalha contra o câncer de próstata
- Loni Anderson, estrela de 'WKRP in Cincinnati', morre aos 79 anos
- O filme \"esquecido\" do universo \"Invocação do Mal\": entenda por que \"A Maldição da Chorona\" é considerado o pior da franquia
- Rose Byrne em colapso: novo filme da A24 é descrito como 'teste de resistência'
- Jornada nas Estrelas: como entender a linha do tempo de uma das maiores sagas da ficção
- Crise na Mubi: cineastas de peso, incluindo israelenses, exigem boicote por laços com investidor militar
- Universo 'Stranger Things' se expande: série animada e peça de teatro são confirmadas
- Wandinha: O que já sabemos sobre a 2ª temporada e os boatos que circulam na internet
- Novo filme de Park Chan-wook, 'No Other Choice', escala festivais e une estrelas
- Homem-Aranha 4: Tom Holland revela novo traje e produção de 'Um Novo Dia' começa com participações surpreendentes
- Quarteto Fantástico segue no topo das bilheterias, mas queda preocupa"""

        EXAMPLE_OUTPUT_11 = """{
   "death_related": false,
   "political_related": false,
   "woke_related": false,
   "spoilers": true,
   "sensitive_theme": false,
   "contains_video": false,
   "is_news_content": true,
   "relevance": "medium",
   "brazil_interest": false,
   "breaking_news": false,
   "audience_age_rating": 10,
   "regional_focus": "global",
   "country_focus": "us",
   "ideological_alignment": "apolitical",
   "entity_type": "series",
   "entity_name": "America's Got Talent",
   "duplication": false
}"""

        EXAMPLE_INPUT_12 = f"""Title: Savannah Guthrie Has Emotional Reunion With Kids Amid ’Today’ Absence
Content: Savannah Guthrie returned to Today‘s Studio 1A on Wednesday, August 6, but not before picking up her kids from summer camp.\n\nThe news anchor enjoyed the end of her two-day Today absence by reuniting with her 10-year-old daughter, Vale, and 8-year-old son, Charley. Guthrie shared several photos from the camp pick-up via her Instagram Story on Tuesday, August 5, including individual snaps of herself hugging each of her children and a group selfie the three of them took together.\n\nShe also poked fun at her children by criticizing their hygiene habits. “There is no greater act of motherly love than touching the post-camp retainer 🤢,” she hilariously wrote over a snap of one of the kids’
Last titles:
- Wild Bill Wichrowski do 'Deadliest Catch' ficará de fora da 21ª temporada após batalha contra o câncer de próstata
- Loni Anderson, estrela de 'WKRP in Cincinnati', morre aos 79 anos
- O filme \"esquecido\" do universo \"Invocação do Mal\": entenda por que \"A Maldição da Chorona\" é considerado o pior da franquia
- Rose Byrne em colapso: novo filme da A24 é descrito como 'teste de resistência'
- Jornada nas Estrelas: como entender a linha do tempo de uma das maiores sagas da ficção
- Crise na Mubi: cineastas de peso, incluindo israelenses, exigem boicote por laços com investidor militar
- Universo 'Stranger Things' se expande: série animada e peça de teatro são confirmadas
- Wandinha: O que já sabemos sobre a 2ª temporada e os boatos que circulam na internet
- Novo filme de Park Chan-wook, 'No Other Choice', escala festivais e une estrelas
- Homem-Aranha 4: Tom Holland revela novo traje e produção de 'Um Novo Dia' começa com participações surpreendentes
- Quarteto Fantástico segue no topo das bilheterias, mas queda preocupa
- Novo filme de Jim Jarmusch com Adam Driver e Cate Blanchett será distribuído pela MUBI
- Tulsa King: 3ª temporada com Sylvester Stallone ganha data de estreia e primeiras imagens"""

        EXAMPLE_OUTPUT_12 = """{
    "death_related": false,
    "political_related": false,
    "woke_related": false,
    "spoilers": false,
    "sensitive_theme": false,
    "contains_video": false,
    "is_news_content": true,
    "relevance": "medium",
    "brazil_interest": false,
    "breaking_news": false,
    "audience_age_rating": 10,
    "regional_focus": "americas",
    "country_focus": "us",
    "ideological_alignment": "apolitical",
    "entity_type": "person",
    "entity_name": "Savannah Guthrie",
    "duplication": false
}"""

        # Estrutura de conversação correta com múltiplos exemplos
        # Estrutura de conversação correta com múltiplos exemplos
        contents = [
            # Primeiro exemplo
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=EXAMPLE_INPUT_1)
                ]
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text=EXAMPLE_OUTPUT_1)
                ]
            ),
            # Segundo exemplo
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=EXAMPLE_INPUT_2)
                ]
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text=EXAMPLE_OUTPUT_2)
                ]
            ),
            # Terceiro exemplo
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=EXAMPLE_INPUT_3)
                ]
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text=EXAMPLE_OUTPUT_3)
                ]
            ),
            # Quarto exemplo
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=EXAMPLE_INPUT_4)
                ]
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text=EXAMPLE_OUTPUT_4)
                ]
            ),
            # Quinto exemplo
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=EXAMPLE_INPUT_5)
                ]
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text=EXAMPLE_OUTPUT_5)
                ]
            ),
            # Sexto exemplo
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=EXAMPLE_INPUT_6)
                ]
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text=EXAMPLE_OUTPUT_6)
                ]
            ),
            # Sétimo exemplo
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=EXAMPLE_INPUT_7)
                ]
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text=EXAMPLE_OUTPUT_7)
                ]
            ),
            # Oitavo exemplo
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=EXAMPLE_INPUT_8)
                ]
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text=EXAMPLE_OUTPUT_8)
                ]
            ),
            # Nono exemplo
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=EXAMPLE_INPUT_9)
                ]
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text=EXAMPLE_OUTPUT_9)
                ]
            ),
            # Décimo exemplo
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=EXAMPLE_INPUT_10)
                ]
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text=EXAMPLE_OUTPUT_10)
                ]
            ),
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=EXAMPLE_INPUT_11)
                ]
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text=EXAMPLE_OUTPUT_11)
                ]
            ),
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=EXAMPLE_INPUT_12)
                ]
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text=EXAMPLE_OUTPUT_12)
                ]
            ),
            # Agora o usuário envia a notícia real para ser analisada
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=f"""Title: {title}
Content: {content}
Last titles:
- {last_titles_formatted}""")
                ]
            )
        ]

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTIONS,
            response_mime_type="text/plain",
            max_output_tokens=1024,
            temperature=0.3,
        )

        response_text = ""
        for chunk in client.models.generate_content_stream(model=model, contents=contents, config=config):
            if chunk.text:
                response_text += chunk.text
        
        json_result = extract_json(response_text)
        parsed = json.loads(json_result)

        ALLOWED_KEYS = {
            "death_related", "political_related", "woke_related", "spoilers", 
            "sensitive_theme", "contains_video", "is_news_content", "relevance",
            "brazil_interest", "breaking_news", "audience_age_rating", "regional_focus",
            "country_focus", "ideological_alignment", "entity_type", "entity_name", "duplication"
        }

        return {"filter": {key: parsed[key] for key in ALLOWED_KEYS if key in parsed}}

    except Exception as e:
        raise ValueError(f"Erro na filtragem: {str(e)}")

def should_skip_insertion(filters: dict) -> tuple[bool, str]:
    if filters.get("duplication", False):
        return True, "duplicação detectada"
    if not filters.get("is_news_content", True):
        return True, "conteúdo não é notícia"
    if not filters.get("brazil_interest", True):
        return True, "baixo interesse para o Brasil"
    if filters.get("relevance", "") not in {"medium", "high", "viral"}:
        return True, f"relevância insuficiente ({filters.get('relevance')})"
    return False, ""

app = FastAPI(title="News Filter API")
router = APIRouter()

@router.post("/filter")
async def filter_endpoint():
    news_data = None
    news_id = None
    
    try:
        news_data = await fetch_unused_news()
        
        title = news_data.get("title", "")
        url = news_data.get("url", "")
        news_id = news_data.get("news_id", "")
        image_url = news_data.get("image", "")
        
        if not title.strip() or not url.strip():
            raise ValueError("Title e URL não podem estar vazios")
        
        last_titles = await fetch_last_50_titles()
        full_text = await extract_article_text(url)
        
        if not full_text.strip():
            raise ValueError("Não foi possível extrair texto da URL")
        
        filter_result = await filter_news(title, full_text, last_titles)
        should_skip, skip_reason = should_skip_insertion(filter_result["filter"])
        
        if should_skip:
            await mark_news_as_used(news_id)
            return {
                "filter": filter_result["filter"],
                "title_en": title,
                "text_en": full_text,
                "news_id": news_id,
                "url": url,
                "image": image_url,
                "skipped": True,
                "skip_reason": skip_reason
            }
        else:
            await insert_news_to_db(title, full_text, news_id, url, image_url, filter_result["filter"])
            await mark_news_as_used(news_id)
            
            return {
                "filter": filter_result["filter"],
                "title_en": title,
                "text_en": full_text,
                "news_id": news_id,
                "url": url,
                "image": image_url,
                "skipped": False
            }
        
    except Exception as e:
        error_msg = str(e)
        
        if news_id:
            await mark_news_as_used(news_id)
        
        if "Nenhuma notícia disponível" in error_msg:
            raise HTTPException(status_code=404, detail=error_msg)
        elif "Title e URL não podem estar vazios" in error_msg:
            raise HTTPException(status_code=400, detail=error_msg)
        elif "Não foi possível extrair texto" in error_msg:
            raise HTTPException(status_code=400, detail=error_msg)
        else:
            raise HTTPException(status_code=500, detail=f"Erro interno: {error_msg}")

app.include_router(router)
