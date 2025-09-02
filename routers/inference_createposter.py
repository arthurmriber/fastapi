import os
import logging
import re
import json
from urllib.parse import urlencode, quote, parse_qs, urlparse, urlunparse
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from google import genai
from google.genai import types

# Configurar logging
logger = logging.getLogger(__name__)

router = APIRouter()

class PosterRequest(BaseModel):
    content: str

class PosterResponse(BaseModel):
    result: dict
    urls: list

def clean_json_string(json_string: str) -> str:
    """
    Remove caracteres de controle inválidos do JSON antes do parse.
    """
    if not json_string:
        return json_string
    
    # Remove caracteres de controle (exceto \t, \n, \r que são válidos em JSON)
    # mas precisamos escapar corretamente dentro das strings
    cleaned = ""
    i = 0
    in_string = False
    escape_next = False
    
    while i < len(json_string):
        char = json_string[i]
        
        if escape_next:
            # Se o caractere anterior foi \, adiciona este caractere escapado
            cleaned += char
            escape_next = False
        elif char == '\\' and in_string:
            # Caractere de escape dentro de string
            cleaned += char
            escape_next = True
        elif char == '"' and not escape_next:
            # Início ou fim de string (se não estiver escapado)
            cleaned += char
            in_string = not in_string
        elif in_string:
            # Dentro de string - tratar caracteres especiais
            if ord(char) < 32 and char not in ['\t']:  # Remove controles exceto tab
                if char == '\n':
                    cleaned += '\\n'  # Escapa quebra de linha
                elif char == '\r':
                    cleaned += '\\r'  # Escapa carriage return
                else:
                    # Remove outros caracteres de controle
                    pass
            else:
                cleaned += char
        else:
            # Fora de string - remove apenas caracteres de controle problemáticos
            if ord(char) >= 32 or char in ['\t', '\n', '\r', ' ']:
                cleaned += char
        
        i += 1
    
    return cleaned

def fix_citation_quotes(citation_text: str) -> str:
    """
    Corrige as aspas no texto de citação:
    - Se não tiver aspas no início e fim, adiciona " "
    - Se tiver aspas comuns ou outras, substitui por " "
    - Remove todas as tags HTML
    """
    if not citation_text or citation_text.strip() == "":
        return citation_text

    text = citation_text.strip()
    
    # Remover todas as tags HTML
    text = re.sub(r'<[^>]+>', '', text)
    
    # Verificar se já tem as aspas corretas
    if text.startswith('“') and text.endswith('”'):
        return text
    
    # Remover aspas existentes do início e fim
    quote_chars = ['"', "'", '"', '"', ''', ''', '❝', '❞']
    
    # Remover aspas do início
    while text and text[0] in quote_chars:
        text = text[1:]
    
    # Remover aspas do fim
    while text and text[-1] in quote_chars:
        text = text[:-1]
    
    # Adicionar as aspas corretas
    return f"“{text.strip()}”"

def clean_text_content_for_text_param(text: str) -> str:
    """
    Limpa o conteúdo do parâmetro 'text':
    - Remove apenas tags <wiki>
    - Mantém <strong> e <em>
    - Se tiver tags aninhadas (ex: <strong><em>), prioriza a segunda (mais interna)
    """
    if not text:
        return text
    
    # Primeiro, resolver conflitos de tags aninhadas - priorizar a segunda (mais interna)
    # <strong><em>conteúdo</em></strong> -> <em>conteúdo</em>
    text = re.sub(r'<strong>\s*<em>(.*?)</em>\s*</strong>', r'<em>\1</em>', text)
    # <em><strong>conteúdo</strong></em> -> <strong>conteúdo</strong>
    text = re.sub(r'<em>\s*<strong>(.*?)</strong>\s*</em>', r'<strong>\1</strong>', text)
    
    # Remover apenas tags <wiki>
    text = re.sub(r'</?wiki[^>]*>', '', text)
    
    return text.strip()

def clean_text_content_remove_all_tags(text: str) -> str:
    """
    Remove TODAS as tags HTML do texto (para headline, title, citation).
    Mantém apenas o conteúdo textual limpo.
    """
    if not text:
        return text
    
    # Remove TODAS as tags HTML usando regex mais ampla
    text = re.sub(r'<[^>]*>', '', text)
    
    # Remove possíveis entidades HTML comuns
    text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
    text = text.replace('&quot;', '"').replace('&#39;', "'")
    
    return text.strip()

def clean_text_content(text: str) -> str:
    """
    Função mantida para compatibilidade com o código existente.
    Limpa o conteúdo de texto removendo tags inválidas e corrigindo formatação:
    - Remove todas as tags exceto <strong> e <em>
    - Se tiver <strong><em> juntas, prioriza <em>
    - Se tiver <em><strong> juntas, prioriza <strong>
    """
    return clean_text_content_for_text_param(text)

def fix_url_citation(url: str) -> str:
    """
    Analisa uma URL e trata os parâmetros de texto de forma específica:
    - Para 'text': mantém <strong> e <em>, remove <wiki>, resolve conflitos de tags aninhadas
    - Para 'headline', 'title', 'citation': remove TODAS as tags HTML
    """
    try:
        # Parse da URL
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        
        # Parâmetros que devem ter TODAS as tags removidas
        clean_all_params = ['headline', 'title', 'citation']
        
        # Parâmetros que têm tratamento especial (apenas text)
        special_text_params = ['text']
        
        # Processar parâmetros que devem ser completamente limpos
        for param in clean_all_params:
            if param in query_params and query_params[param]:
                original_text = query_params[param][0]
                cleaned_text = clean_text_content_remove_all_tags(original_text)
                
                # Se for citation, aplicar correção específica das aspas
                if param == 'citation':
                    cleaned_text = fix_citation_quotes(cleaned_text)
                
                query_params[param] = [cleaned_text]
        
        # Processar parâmetro 'text' com tratamento especial
        for param in special_text_params:
            if param in query_params and query_params[param]:
                original_text = query_params[param][0]
                cleaned_text = clean_text_content_for_text_param(original_text)
                query_params[param] = [cleaned_text]
        
        # Reconstruir a query string
        new_query = urlencode(
            {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in query_params.items()},
            quote_via=quote
        )
        
        # Reconstruir a URL
        new_parsed_url = parsed_url._replace(query=new_query)
        return urlunparse(new_parsed_url)
    
    except Exception as e:
        logger.warning(f"Erro ao processar URL para correção de texto: {e}")
        return url

def format_url(base_url: str, endpoint: str, params: dict) -> str:
    """
    Formata uma URL completa com os parâmetros dados
    """
    # URL base + endpoint
    full_url = f"{base_url.rstrip('/')}{endpoint}"
    
    # Adicionar image_url padrão
    url_params = {"image_url": "https://placehold.co/1080x1350.png"}
    
    # Adicionar outros parâmetros
    for key, value in params.items():
        if value is not None:
            url_params[key] = str(value)
    
    # Construir query string
    query_string = urlencode(url_params, quote_via=quote)
    return f"{full_url}?{query_string}"

def generate_urls_from_result(result: dict, base_url: str = "https://habulaj-newapi-clone3.hf.space") -> list:
    """
    Gera as URLs formatadas a partir do resultado JSON
    """
    urls = []
    
    # Se for notícia simples
    if "endpoint" in result and "params" in result:
        url = format_url(base_url, result["endpoint"], result["params"])
        # Corrigir citation na URL se presente
        url = fix_url_citation(url)
        urls.append(url)
    
    # Se for carrossel com capa e slides
    elif "cover" in result or "slides" in result:
        # Adicionar URL da capa
        if "cover" in result:
            cover_url = format_url(
                base_url,
                result["cover"]["endpoint"],
                result["cover"]["params"]
            )
            # Corrigir citation na URL se presente
            cover_url = fix_url_citation(cover_url)
            urls.append(cover_url)
        
        # Adicionar URLs dos slides
        if "slides" in result:
            for slide in result["slides"]:
                slide_url = format_url(
                    base_url,
                    slide["endpoint"],
                    slide["params"]
                )
                # Corrigir citation na URL se presente
                slide_url = fix_url_citation(slide_url)
                urls.append(slide_url)
    
    return urls

@router.post("/generate-poster", response_model=PosterResponse)
async def generate_poster(request: PosterRequest):
    """
    Endpoint para gerar posters automáticos (notícias ou carrosséis) usando o modelo Gemini.
    """
    try:
        # Verificar API key
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise HTTPException(status_code=500, detail="API key não configurada")
        
        client = genai.Client(api_key=api_key)
        model = "gemini-2.5-flash"
        
        # System instructions
        SYSTEM_INSTRUCTIONS = """
Você é uma IA especializada em gerar posters automáticos (notícias ou carrosséis de slides) a partir de texto noticioso fornecido.

1. Analise o texto recebido.
2. Se for uma notícia simples e direta → gere um único JSON correspondente ao endpoint /cover/news.
3. Se o texto for longo, didático, explicativo, ou parecer adequado para um carrossel de slides → gere um único JSON contendo:
   - O objeto da capa (/create/cover/image)
   - Uma lista slides com cada slide (/create/image).

📌 Estrutura esperada do JSON

1. Notícia simples → /cover/news

{
  "endpoint": "/cover/news",
  "params": {
    "headline": "Título da notícia seguindo padrão brasileiro",
    "text_position": "bottom"
  },
  "instagram_description": "Descrição aqui"
}

2. Carrossel de slides → capa + slides

{
  "cover": {
    "endpoint": "/create/cover/image",
    "params": {
      "title": "Título principal seguindo padrão brasileiro",
      "title_position": "bottom"
    }
  },
  "slides": [
    {
      "endpoint": "/create/image",
      "params": {
        "text": "Texto do primeiro slide (aceita <strong> e <em>)",
        "text_position": "bottom",
        "citation": null
      }
    },
    {
      "endpoint": "/create/image",
      "params": {
        "text": "Texto do segundo slide",
        "text_position": "bottom",
        "citation": "Citação curta e direta (opcional)",
        "citation_direction": "text-top"
      }
    }
  ],
  "instagram_description": "Descrição aqui"
}

🎯 Regras importantes para títulos e textos:
- TÍTULOS (headline, title): Siga o padrão brasileiro de capitalização:
  * Primeira letra maiúscula
  * Demais palavras em minúsculo
  * EXCETO: nomes próprios, títulos de filmes, séries, livros, bandas, etc.
  * Exemplo: "Lady Gaga anuncia novo álbum 'Chromatica Ball'"
  * Exemplo: "Netflix cancela série 'The OA' após duas temporadas"

- CITAÇÕES (citation): 
  * Devem ser CURTAS e DIRETAS (máximo 60 caracteres)
  * Use apenas para frases impactantes, declarações ou destaques
  * NÃO use tags HTML em citações
  * Exemplo bom: "Foi uma experiência transformadora"
  * Exemplo ruim: "Esta foi realmente uma experiência muito transformadora que mudou completamente a minha vida"

- FORMATAÇÃO DE TEXTO:
  * Use apenas <strong> para negrito e <em> para itálico
  * NÃO use outras tags como <wiki>, <span>, etc.
  * Se precisar enfatizar algo, escolha entre <strong> OU <em>, não ambos juntos

Nunca utilize as palavras "icônico" ou "icônicos" ao se referir a pessoas, acontecimentos ou objetos neste contexto. O jornal em questão é um veículo de direita, com um público majoritariamente conservador, e esse termo pode soar inadequado ou destoar do tom editorial esperado.
Em vez disso, prefira sempre sinônimos como "lendário", "lendários", "memorável", "histórico" ou outros termos que transmitam grandeza e relevância, mas mantendo a coerência com a linha editorial conservadora.

- DESCRIÇÃO PRO INSTAGRAM:

Resumo de 2-3 parágrafos com os principais pontos da notícia, mas sem revelar tudo. Termine SEMPRE com uma chamada como "🔗 Leia mais sobre [tema] no link da nossa bio." ou variação similar. Nunca utilize exclamações no link da bio. Adicione no máximo 5 hashtags no final.

Não use palavras genéricas ou pontuações genéricas na geração da descrição pro instagram. Evite exclamações, emojis e sempre respeite os espaços. Nas hashtags, sempre inclua hashtags diretas e populares.

Valores possíveis:

/cover/news
text_position = top | bottom

/create/image
text_position = top | bottom
citation_direction = top | bottom | text-top | text-bottom

/create/cover/image
title_position = top | bottom

    IMPORTANTE: Retorne apenas o JSON válido, sem explicações adicionais. Além disso, para caso de slides, deve ser 9 slides no máximo. 10 contando com a capa.
"""

        # Exemplo 1 - Input do usuário
        exemplo_1_input = """Saltando para um pequeno palco no sul da Califórnia, poderia ser apenas o elenco de qualquer peça escolar nos Estados Unidos.

A jornada deles até a noite de estreia, no entanto, foi marcada por incêndio e perda. 
O incêndio Eaton destruiu sua escola. Eles criaram um novo País das Maravilhas no palco.

Todos os sábados desta primavera, dezenas de crianças se reuniam no ginásio de uma escola em Pasadena, Califórnia. Sentavam-se de pernas cruzadas, segurando seus roteiros, enquanto as falas de "Alice no País das Maravilhas" ecoavam pelas paredes.

Nos limites dos ensaios semanais, a vida parecia quase normal. Mas fora dali, elas lidavam com perdas em escala devastadora.

Em janeiro, o incêndio Eaton destruiu a escola primária deles — uma série de salas cercadas por jardins e pomares, nos pés das montanhas San Gabriel, em Altadena.

O fogo consumiu as casas de pelo menos sete integrantes do elenco e deixou outras inabitáveis. Dezenas de colegas partiram para outras escolas, estados e até países.

O incêndio também destruiu o palco, forçando os ensaios a acontecerem em uma quadra de basquete, com iluminação fluorescente e acústica estranha.

Passamos cinco meses acompanhando esse grupo de cerca de 40 alunos, enquanto se preparavam para a grande apresentação.

Para muitos, os ensaios semanais lembravam os de antes do incêndio. Eles pintavam cenários, lanchavam bananas e chips de churrasco.

E mergulhavam na história da estranha jornada de Alice por um buraco profundo e sombrio, rumo a um lugar onde nada fazia sentido.

Pergunte a qualquer pai ou professor: a Aveson School of Leaders tinha o campus mais bonito do condado de Los Angeles. Era uma rede de prédios de estuque colorido, com jardins e pátios. Algumas aulas eram dadas em uma tenda. Os alunos criavam galinhas no jardim.

"Era um pedaço de paraíso", disse Daniela Anino, diretora do colégio.

O incêndio Eaton transformou tudo em ruínas carbonizadas. As galinhas também morreram.

Para Cecily Dougall, os dias após o fogo foram um borrão. Sua casa sobreviveu, mas quase todo o resto se perdeu.

"Foi a primeira experiência assustadora que tive", disse Cecily, de 10 anos. "Nem sei por que essas coisas acontecem."

No início, parecia impensável que o musical da primavera fosse acontecer. Mas a direção decidiu rapidamente que deveria continuar.

"Todos acreditamos que as artes são cruciais para a vida, especialmente para processar algo tão traumático", disse Jackie Gonzalez-Durruthy, da ONG Arts Bridging the Gap, que ajuda a manter o programa de teatro da escola.

Quando chegaram as audições, em fevereiro, Cecily (que usa pronomes neutros) não quis cantar. Sentia que sua voz estava trêmula, refletindo medo e tristeza.

**Um farol de normalidade**

Os ensaios mudaram-se para o ginásio do campus de ensino médio da Aveson, em Pasadena. Ali, os atores marcavam cenas sobre as linhas da quadra de basquete, e as tabelas serviam de coxias improvisadas.

A rotina dos ensaios de sábado virou um fio de esperança para muitas famílias — um lembrete de como as coisas eram antes.

"É praticamente igual a quando fizemos Matilda e Shrek", disse Lila Avila-Brewster, de 10 anos, cuja família perdeu a casa no incêndio. "Parece bem parecido."

Para a mãe de Lila, Paloma Avila (que usa pronomes neutros), os encontros eram também uma rede de apoio.

"Era tipo: 'Quem precisa de sapatos? Quem precisa de escovas de dente?'", contou.

Lila queria ser o Gato de Cheshire, mas acabou escalada como Petúnia — uma das flores que zombam de Alice. Depois percebeu que gostava mais desse papel. "As flores são metidas e acham que são melhores que todo mundo", disse.

Já no fim de março, a tristeza que marcou a audição de Cecily já não era tão sufocante. Elu abraçou o papel do Chapeleiro Maluco, memorizando falas e músicas com tanta precisão que Gonzalez-Durruthy chamou elu de "pequeno metrônomo".

Annika, irmã mais velha de Cecily, ouviu pais comentando sobre o quanto as crianças tinham sofrido. Mas discordou.

"Isso é só com o que estamos lidando", disse.

Para Eden Javier, de 11 anos, os ensaios eram divertidos, mas ela sentia falta do palco. "É como se você tivesse poder quando está lá em cima", disse. No chão do ginásio, era mais difícil imaginar o País das Maravilhas.

A perda do palco parecia pequena diante de tantas outras, mas ainda assim doía. O trabalho escolar de Eden sobre cegueira queimou junto com a escola. As novas salas de aula eram estranhas. Amigos deixaram a Aveson.

Em aula, ela escreveu uma ode a algo que o fogo havia levado:

"O palco, o palco, / meu lugar de conforto, / o palco, o palco, / meu lugar de confiança. / O palco, o palco. / Já não está aqui."

Mike Marks, diretor e professor de teatro da Aveson, também foi deslocado pelos incêndios, mas estava determinado a achar um palco. Ligou para todos os teatros, igrejas e escolas que conhecia. Duas semanas depois, a vizinha Barnhart School ofereceu o auditório.

Quando Marks entrou e viu os alunos rindo e correndo em círculos, sentiu como se o tempo tivesse voltado.

"Se eu não soubesse que uma catástrofe enorme tinha acontecido aqui", disse, "nem teria percebido diferença."""

        # Exemplo 1 - Output esperado
        exemplo_1_output = """{
  "cover": {
    "endpoint": "/create/cover/image",
    "params": {
      "title": "O incêndio em Eaton destruiu a escola deles. Eles criaram um novo mundo encantado no palco.",
      "title_position": "top"
    }
  },
  "slides": [
    {
      "endpoint": "/create/image",
      "params": {
        "text": "Em janeiro, o incêndio de Eaton devastou a escola primária Aveson School of Leaders. Parecia impensável que o musical da primavera acontecesse, mas a direção da escola rapidamente decidiu que ele deveria continuar.",
        "text_position": "bottom",
        "citation": null
      }
    },
    {
      "endpoint": "/create/image",
      "params": {
        "text": "E assim, dezenas de crianças começaram a se reunir no ginásio da escola secundária e do ensino médio da Aveson todos os sábados. Sentavam-se de pernas cruzadas, segurando seus roteiros, enquanto as falas de Alice no País das Maravilhas ecoavam pelas paredes.",
        "text_position": "top",
        "citation": null
      }
    },
    {
      "endpoint": "/create/image",
      "params": {
        "text": "O incêndio havia consumido as casas de pelo menos sete membros do elenco e tornado outras inabitáveis. Dezenas de colegas deixaram a cidade ou se mudaram, como a <strong>Ruby Hull</strong> — escalada para viver a Pequena Alice — cuja família se mudou seis horas ao norte.",
        "text_position": "bottom",
        "citation": null
      }
    },
    {
      "endpoint": "/create/image",
      "params": {
        "text": "Para <strong>Paloma Ávila</strong> — mãe de <strong>Lila</strong>, escalada para viver Petúnia — a rotina dos ensaios de sábado se tornou um ponto de apoio para reencontrar outros pais depois de perder a casa, e também uma lembrança de como as coisas costumavam ser.",
"text_position": "bottom",
        "citation": "Era assim: 'Ok, quem precisa de sapatos? Quem precisa de escovas de dente?'",
        "citation_direction": "top"
      }
    }
  ],
  "instagram_description": "Para as crianças da Aveson School of Leaders em Altadena, Califórnia, a vida parecia quase normal durante os ensaios do espetáculo. Mas fora da escola, elas enfrentavam perdas em uma escala impressionante. Em janeiro, o incêndio em Eaton destruiu a escola primária e as casas de muitos estudantes. Eles planejavam apresentar “Alice no País das Maravilhas” e, apesar do caminho de recuperação, os líderes da escola acharam que valia a pena descobrir como garantir que o espetáculo acontecesse.\n\n🔗 Confira a jornada completa no link que está na nossa bio.\n\n#AliceNoPaísDasMaravilhas #IncêndioEaton #Resiliência #Teatro #California"
}"""
        exemplo_2_input = """
        Antes de conquistar sua primeira indicação ao Emmy por “Severance”, Zach Cherry passava seus dias em um escritório em Manhattan. O ator trabalhou durante anos como gerente em uma organização sem fins lucrativos, função que lhe permitia conciliar a rotina administrativa com sua verdadeira paixão: a comédia de improviso.

Cherry, hoje com 37 anos, começou a se dedicar ao improviso ainda na adolescência, em acampamentos e na escola, continuando na faculdade em Amherst. Depois da graduação, participou ativamente do circuito nova-iorquino, especialmente no Upright Citizens Brigade Theater, enquanto buscava papéis em produções de TV e cinema.

Aos poucos, foi conquistando espaço em séries como Crashing, produzida por Judd Apatow, onde interpretou um gerente atrapalhado, e em You, thriller exibido pela Lifetime e Netflix. Foi nesse momento que percebeu que poderia finalmente viver da atuação. “Achei que o valor pago por episódio seria pelo trabalho inteiro da temporada e mesmo assim fiquei animado. Percebi que poderia fazer disso minha profissão”, recorda.

No cinema, Cherry também participou de filmes como Homem-Aranha: De Volta ao Lar, mas foi em Severance, da Apple TV+, que alcançou maior destaque. Na série, ele interpreta Dylan G., um funcionário da misteriosa Lumon Industries, papel que lhe rendeu uma indicação ao Emmy de melhor ator coadjuvante em drama. A produção soma 27 indicações e colocou Cherry ao lado de nomes como Adam Scott, Christopher Walken, John Turturro e Tramell Tillman.

Apesar da confiança, o ator admite sentir a pressão no set, principalmente fora do gênero cômico. “Na comédia, eu sei quando estou indo bem ou não. Mas em algo como Severance é um salto maior de fé”, disse. Na segunda temporada, lançada em janeiro, seu personagem vive desde momentos íntimos, como cenas com Merritt Wever, até aventuras físicas em locações como o Minnewaska State Park, em Nova York.

De um escritório real para o fictício e perturbador ambiente de Severance, Zach Cherry mostra que a disciplina do passado e a paixão pelo improviso foram essenciais para chegar ao momento mais marcante de sua carreira.
        """

        exemplo_2_output = """
        {
  "endpoint": "/cover/news",
  "params": {
    "headline": "'Ruptura' foi um salto de fé para Zach Cherry",
    "text_position": "bottom"
  },
  "instagram_description": "Antes de conquistar sua primeira indicação ao Emmy por 'Severance', Zach Cherry conciliava seu trabalho em Manhattan com a paixão pelo improviso. Atuando em séries como 'Crashing' e 'You', e no cinema em 'Homem-Aranha: De Volta ao Lar', ele encontrou reconhecimento ao interpretar Dylan G., funcionário da Lumon Industries, em 'Severance', papel que lhe rendeu a primeira indicação ao Emmy.\n\n🔗 Leia mais sobre a trajetória de Zach Cherry no link da nossa bio.\n\n#Severance #ZachCherry #AppleTV #Emmy #Ator #Carreira #TVeCinema"
}
"""
        exemplo_3_input = """
        8 Mulheres, 4 Quartos e 1 Causa: Quebrando o Teto de Vidro da IA\n\nFoundHer House, uma casa em Glen Park, São Francisco, é uma rara residência de hackers totalmente feminina, onde as moradoras criam uma comunidade de apoio para desenvolver suas startups.\n\nEm uma tarde recente, Miki Safronov-Yamamoto, 18 anos, e algumas colegas sentaram-se em cadeiras diferentes ao redor da mesa de jantar de sua casa de dois andares. Entre enviar e-mails e checar mensagens no LinkedIn, discutiam como organizar um “demo day”, onde mostrariam suas startups para investidores.\n\nMiki, a mais jovem da casa e caloura na University of Southern California, sugeriu que discutissem discretamente a duração das apresentações — talvez três minutos. Ava Poole, 20 anos, que desenvolve um agente de IA para facilitar pagamentos digitais, perguntou se a plateia seria principalmente de investidores. Miki respondeu que haveria investidores e fundadoras de startups. Chloe Hughes, 21 anos, criando uma plataforma de IA para imóveis comerciais, ouvia música de fundo.\n\nFoundHer House foi criada em maio como uma “hacker house” voltada especificamente para mulheres. O objetivo era criar uma comunidade de apoio para suas oito residentes construírem suas próprias empresas em São Francisco, capital tecnológica dos EUA.\n\nO boom da IA tem sido dominado por homens, e dados mostram que poucas empresas de IA têm fundadoras mulheres. Navrina Singh, CEO da Credo AI, disse que há uma disparidade clara e que as mulheres líderes na área não são bem financiadas. Dos 3.212 acordos de venture capital com startups de IA até meados de agosto de 2025, menos de 20% envolveram empresas com pelo menos uma fundadora mulher.\n\nFoundHer House tentou contrariar essa tendência. Fundada por Miki e Anantika Mannby, 21 anos, estudante da University of Southern California, que desenvolve uma startup de compras digitais, a casa adicionou outras seis residentes, incluindo Ava Poole e Chloe Hughes. As outras são Sonya Jin, 20 anos, criando uma startup para treinar agentes de IA; Danica Sun, 19 anos, trabalhando em energia limpa; Fatimah Hussain, 19 anos, criando um programa de mentoria online; e Naciima Mohamed, 20 anos, desenvolvendo uma ferramenta de IA para ajudar crianças a entender diagnósticos médicos.\n\nApesar dos grandes sonhos, a casa fechará na terça-feira seguinte. Miki, Anantika e quatro outras residentes voltarão para a faculdade; Sonya e Naciima abandonaram os estudos para continuar suas startups. Das oito startups, duas receberam investimento e seis lançaram produtos.\n\nMiki e Anantika criaram FoundHer House ao se mudarem para São Francisco durante o verão. Encontraram um Airbnb acessível em Glen Park, com quatro quartos e três banheiros, alugado por cerca de 40.000 dólares para o verão, com ajuda financeira de investidores. Cada residente paga entre 1.100 e 1.300 dólares de aluguel por mês.\n\nO local tornou-se um ponto de encontro para jantares e discussões de painel patrocinados por firmas de venture capital como Andreessen Horowitz, Bain Capital Ventures e Kleiner Perkins. Organizaram um demo day em 19 de agosto para apresentar suas startups a investidores, com apresentações de quatro minutos para cada residente.\n\nAileen Lee, fundadora da Cowboy Ventures, comentou que foi um dos melhores demo days que já participou, destacando que ainda há muito a melhorar quanto à presença feminina na IA.
        """

        exemplo_3_output = """
        {
  "cover": {
    "endpoint": "/create/cover/image",
    "params": {
      "title": "A casa de hackers só de mulheres que tenta quebrar o teto de vidro da I.A",
      "title_position": "top"
    }
  },
  "slides": [
    {
      "endpoint": "/create/image",
      "params": {
        "text": "A FoundHer House, uma residência no bairro Glen Park em San Francisco, é uma rara casa de hackers só para mulheres, onde as moradoras estão criando uma comunidade de apoio para desenvolver suas startups.",
        "text_position": "bottom",
        "citation": null
      }
    },
    {
      "endpoint": "/create/image",
      "params": {
        "text": "<strong>Ke Naciima Mohamed</strong>, à direita, está desenvolvendo uma ferramenta de I.A. para ajudar crianças a entenderem seus diagnósticos médicos.",
        "text_position": "bottom",
        "citation": "“Eu não queria vir para San Francisco e me isolar enquanto estou construindo.”",
        "citation_direction": "text-top"
      }
    }
  ],
    "instagram_description": "A FoundHer House, uma “hacker house”, é um ambiente de co-living raro em San Francisco voltado especificamente para mulheres. As residentes têm uma comunidade de apoio enquanto constroem suas próprias empresas de tecnologia e economizam com despesas.\n\nÀ medida que o Vale do Silício se agita com jovens que querem trabalhar com inteligência artificial, start-ups emergentes e hacker houses têm sido dominadas por homens, de acordo com investidores e dados de financiamento.\n\n🔗 No link da nossa bio, leia mais sobre as oito residentes e como o boom da I.A. deve perpetuar a demografia da indústria de tecnologia.\n\n#FoundHerHouse #HackerHouse #MulheresNaTecnologia #IA #Startups"
}
        """

        exemplo_4_input = """
Atenção: este artigo contém spoilers importantes sobre o enredo e o final do filme \"Weapons\".\n\nLançado em 8 de agosto, \"Weapons\", o novo filme de Zach Cregger (diretor de \"Bárbaro\"), rapidamente se tornou um sucesso de crítica e bilheteria, arrecadando mais de 199 milhões de dólares em todo o mundo. O longa parte de uma premissa assustadora: em uma noite, às 2:17 da manhã, dezessete crianças da mesma turma escolar acordam, saem de suas casas e desaparecem na escuridão, sem deixar rastros.\n\nA história se desenrola de forma não linear, apresentando os eventos a partir da perspectiva de vários personagens, montando gradualmente o quebra-cabeça para o espectador.\n\nQuem é a vilã e o que ela queria?\n\nA responsável pelo desaparecimento é Gladys (Amy Madigan), tia de Alex (Cary Christopher), o único aluno que não sumiu. Gladys é uma bruxa que precisa drenar a energia vital de outras pessoas para rejuvenescer e sobreviver. Antes do sequestro em massa, ela já havia enfeitiçado os pais de Alex, que permanecem em estado catatônico dentro de casa, servindo como sua primeira fonte de energia.\n\nPara manipular Alex e garantir seu silêncio, Gladys força os pais do garoto a se esfaquearem com garfos. Amedrontado, Alex é coagido a roubar os crachás com os nomes de seus colegas de classe. Usando esses itens pessoais, Gladys lança um feitiço que faz as dezessete crianças correrem para a casa de Alex, onde são mantidas em transe no porão, servindo como \"bateria\" de força vital.\n\nComo o plano é descoberto?\n\nA trama se concentra em três personagens principais: a professora Justine Gandy (Julia Garner), que se torna a principal suspeita da cidade; Archer Graff (Josh Brolin), pai de um dos meninos desaparecidos; e Paul (Alden Ehrenreich), policial e ex-namorado de Justine.\n\nA investigação avança quando Gladys decide eliminar Justine. Usando um feitiço, ela transforma o diretor da escola, Marcus (Benedict Wong), em uma \"arma\" irracional, enviando-o para atacar a professora. Archer testemunha o ataque e, após Marcus ser atropelado e morto, percebe que algo sobrenatural está acontecendo. Ele e Justine se unem e, ao triangular a rota de fuga das crianças, descobrem que todas as direções apontam para a casa de Alex.\n\nO que acontece no final?\n\nAo chegarem à casa, Justine e Archer são atacados por outras pessoas controladas por Gladys, incluindo o policial Paul. Os pais de Alex, também enfeitiçados, tentam matar o próprio filho. Em um ato de desespero, Alex cria um novo encantamento que \"arma\" seus dezessete colegas de classe, direcionando a fúria deles contra Gladys.\n\nA bruxa se torna vítima. Perseguida pelas crianças, Gladys é brutalmente despedaçada. Com sua morte, todos os feitiços são quebrados. Archer, os pais de Alex e os demais enfeitiçados voltam ao normal. Os pais de Alex são internados devido ao trauma, e o garoto passa a viver com outra tia. As crianças são devolvidas às famílias, mas muitas permanecem traumatizadas e sem falar.\n\nSímbolos e perguntas não respondidas\n\nO filme deixa algumas imagens e perguntas em aberto, provocando debates. O horário do desaparecimento, 2:17, é referência ao quarto 217 do livro \"O Iluminado\", de Stephen King. Em um sonho de Archer, um rifle de assalto flutua sobre a casa de Alex, levando a interpretações sobre o título ser uma alegoria a tiroteios em escolas. No entanto, Zach Cregger afirmou que prefere deixar o significado da cena aberto à interpretação do público, em vez de fixá-lo a uma declaração política.
        """

        exemplo_4_output = """
        {
  "endpoint": "/cover/news",
  "params": {
    "headline": "Final explicado de \"Weapons\": o que aconteceu com as crianças?",
    "text_position": "bottom"
  },
  "instagram_description": "Spoilers abaixo!\n\nO filme Weapons, de Zach Cregger, acompanha o desaparecimento de dezessete crianças durante a madrugada, todas manipuladas pela bruxa Gladys (Amy Madigan), tia de Alex (Cary Christopher). Ela drena a energia vital dos alunos para se manter jovem, mantendo-os em transe no porão da casa de Alex, enquanto os pais do garoto também são enfeitiçados.\n\nNo final, Alex cria um feitiço que usa a energia contra Gladys. A bruxa é derrotada após as crianças atacá-la, e todos os feitiços são quebrados. Após seus pais serem internados por causa do trauma, Alex vai morar com outra tia. Muitas crianças permanecem traumatizadas e em silêncio, apesar de todos serem salvos.\n\nO filme contém alusões enigmáticas, como o horário 2:17, que está ligado ao Quarto 217 em O Iluminado, e imagens ambíguas que remetem a discussões sobre violência escolar e ao título. Zach Cregger prefere deixar que o público tire suas próprias conclusões a partir dessas pistas.\n\n🔗 Toda a história e detalhes do final tão no link da bio.\n\n#Weapons #FinalExplicado #ZachCregger #Suspense #Cinema"
}
        """

        exemplo_5_input = """
        A postwar plan for Gaza circulating within the Trump administration, modeled on President Donald Trump’s vow to “take over” the enclave, would turn it into a trusteeship administered by the United States for at least 10 years while it is transformed into a gleaming tourism resort and high-tech manufacturing and technology hub.

The 38-page prospectus seen by The Washington Post envisions at least a temporary relocation of all of Gaza’s more than 2 million population, either through what it calls “voluntary” departures to another country or into restricted, secured zones inside the enclave during reconstruction.

Those who own land would be offered a digital token by the trust in exchange for rights to redevelop their property, to be used to finance a new life elsewhere or eventually redeemed for an apartment in one of six to eight new “AI-powered, smart cities” to be built in Gaza. Each Palestinian who chooses to leave would be given a $5,000 cash payment and subsidies to cover four years of rent elsewhere, as well as a year of food.

The plan estimates that every individual departure from Gaza would save the trust $23,000, compared with the cost of temporary housing and what it calls “life support” services in the secure zones for those who stay.

Called the Gaza Reconstitution, Economic Acceleration and Transformation Trust, or GREAT Trust, the proposal was developed by some of the same Israelis who created and set in motion the U.S.- and Israeli-backed Gaza Humanitarian Foundation (GHF) now distributing food inside the enclave. Financial planning was done by a team working at the time for the Boston Consulting Group.

People familiar with the trust planning and with administration deliberations over postwar Gaza spoke about the sensitive subject on the condition of anonymity. The White House referred questions to the State Department, which declined to comment. BCG has said that work on the trust plan was expressly not approved and that two senior partners who led the financial modeling were subsequently fired.

On Wednesday, Trump held a White House meeting to discuss ideas for how to end the war, now approaching the two-year mark, and what comes next. Participants included Secretary of State Marco Rubio and special presidential envoy Steve Witkoff; former British prime minister Tony Blair, whose views on Gaza’s future have been solicited by the administration; and Trump’ son-in-law Jared Kushner, who handled much of the president’s first-term initiatives on the Middle East and has extensive private interests in the region.

No readout of the meeting or policy decisions were announced, although Witkoff said the night before the gathering that the administration had “a very comprehensive plan.”

It’s not clear if the detailed and comprehensive GREAT Trust proposal is what Trump has in mind. But major elements of it, according to two people familiar with the planning, were specifically designed to make real the president’s vision of a “Riviera of the Middle East.”

Perhaps most appealing, it purports to require no U.S. government funding and offer significant profit to investors. Unlike the controversial and sometimes cash-strapped GHF, which uses armed private U.S. security contractors to distribute food in four southern Gaza locations, the trust plan “does not rely on donations,” the prospectus says. Instead, it would be financed by public and private-sector investment in what it calls “mega-projects,” from electric vehicle plants and data centers to beach resorts and high-rise apartments.
        """

        exemplo_5_output = """
{
  "endpoint": "/cover/news",
  "params": {
    "headline": "Trump planeja tutela dos EUA em Gaza",
    "text_position": "top"
  },
  "instagram_description": "Um plano de pós-guerra para Gaza, circulando dentro da administração Trump e baseado na promessa do presidente Donald Trump de 'assumir' o enclave, transformaria a região em uma tutela administrada pelos Estados Unidos por pelo menos 10 anos, enquanto seria convertida em um luxuoso resort turístico e um polo de manufatura e tecnologia de ponta.\n\nO prospecto de 38 páginas prevê ao menos uma realocação temporária de toda a população de mais de 2 milhões de habitantes de Gaza, seja por meio de partidas 'voluntárias' para outro país, seja para zonas restritas e seguras dentro do enclave durante a reconstrução.\n\nO plano estima que cada saída individual de Gaza economizaria à tutela US$ 23.000, em comparação com o custo de habitação temporária e serviços de 'suporte à vida' nas zonas seguras para aqueles que permanecem.\n\nNão está claro se a proposta detalhada e abrangente é exatamente o que Trump tem em mente, mas elementos principais foram projetados para tornar real a visão do presidente de uma 'Riviera do Oriente Médio'.\n\n🔗 Leia toda a história no link da bio.\n\n#Gaza #Trump #Politica #Guerra #OrienteMedio"
}
        """
        
        # Configuração da geração
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTIONS,
            response_mime_type="application/json",
            max_output_tokens=8000,
            temperature=0.7,
        )
        
        # Conteúdo da conversa com exemplos few-shot
        contents = [
            # Exemplo 1 - User
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=exemplo_1_input)]
            ),
            # Exemplo 1 - Assistant (Model)
            types.Content(
                role="model",
                parts=[types.Part.from_text(text=exemplo_1_output)]
            ),
            # Exemplo 2 - User
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=exemplo_2_input)]
            ),
            # Exemplo 2 - Assistant (Model)
            types.Content(
                role="model",
                parts=[types.Part.from_text(text=exemplo_2_output)]
            ),
            # Exemplo 3 - User
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=exemplo_3_input)]
            ),
            # Exemplo 3 - Assistant (Model)
            types.Content(
                role="model",
                parts=[types.Part.from_text(text=exemplo_3_output)]
            ),
            # Exemplo 4 - User
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=exemplo_4_input)]
            ),
            # Exemplo 4 - Assistant (Model)
            types.Content(
                role="model",
                parts=[types.Part.from_text(text=exemplo_4_output)]
            ),
            # Exemplo 5 - User
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=exemplo_5_input)]
            ),
            # Exemplo 5 - Assistant (Model)
            types.Content(
                role="model",
                parts=[types.Part.from_text(text=exemplo_5_output)]
            ),
            # Input real do usuário
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=request.content)]
            )
        ]
        
        # Gerar conteúdo
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config
        )

        logger.info("Resposta do modelo recebida com sucesso")

        # Extrair texto da resposta
        response_text = ""
        if hasattr(response, 'text') and response.text:
            response_text = response.text
        elif hasattr(response, 'candidates') and response.candidates:
            for candidate in response.candidates:
                if hasattr(candidate, 'content') and candidate.content:
                    if hasattr(candidate.content, 'parts') and candidate.content.parts:
                        for part in candidate.content.parts:
                            if hasattr(part, 'text') and part.text:
                                response_text += part.text

        if not response_text or response_text.strip() == "":
            logger.error("Resposta do modelo está vazia")
            raise HTTPException(
                status_code=500,
                detail="Modelo não retornou conteúdo válido"
            )

        # Limpar caracteres de controle antes do parse
        clean_response = clean_json_string(response_text)
        
        # Parse do JSON
        try:
            result_json = json.loads(clean_response)
        except json.JSONDecodeError as e:
            logger.error(f"Erro ao fazer parse do JSON: {e}")
            logger.error(f"Resposta original: {response_text}")
            logger.error(f"Resposta limpa: {clean_response}")
            
            # Tentar uma limpeza mais agressiva como fallback
            try:
                # Remove quebras de linha e espaços extras
                fallback_clean = re.sub(r'\s+', ' ', response_text.strip())
                # Remove caracteres de controle
                fallback_clean = ''.join(char for char in fallback_clean if ord(char) >= 32 or char in [' ', '\t'])
                result_json = json.loads(fallback_clean)
                logger.info("Parse bem-sucedido com limpeza de fallback")
            except json.JSONDecodeError as fallback_error:
                logger.error(f"Erro no fallback também: {fallback_error}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Resposta do modelo não é um JSON válido: {str(e)}"
                )

        # Gerar URLs formatadas
        formatted_urls = generate_urls_from_result(result_json)

        logger.info("Processamento concluído com sucesso")
        logger.info(f"URLs geradas: {formatted_urls}")

        return PosterResponse(result=result_json, urls=formatted_urls)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro na geração do poster: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))