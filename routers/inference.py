import os
import logging
import json
import requests
import importlib.util
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from google import genai
from google.genai import types
from datetime import datetime
from zoneinfo import ZoneInfo
import locale
import re
import asyncio
from typing import Optional, Dict, Any

# Configurar logging
logger = logging.getLogger(__name__)

router = APIRouter()

class NewsRequest(BaseModel):
    content: str
    file_id: str = None  # Agora opcional

class NewsResponse(BaseModel):
    title: str
    subhead: str
    content: str
    sources_info: Optional[Dict[str, Any]] = None  # Informações das fontes geradas

# Referência ao diretório de arquivos temporários
TEMP_DIR = Path("/tmp")

def load_searchterm_module():
    """Carrega o módulo searchterm.py dinamicamente"""
    try:
        # Procura o arquivo searchterm.py em diferentes locais
        searchterm_path = Path(__file__).parent / "searchterm.py"
        
        if not searchterm_path.exists():
            # Tenta outros caminhos possíveis
            possible_paths = [
                Path(__file__).parent.parent / "searchterm.py",
                Path("./searchterm.py"),
                Path("../searchterm.py")
            ]
            
            for path in possible_paths:
                if path.exists():
                    searchterm_path = path
                    break
            else:
                logger.error("searchterm.py não encontrado em nenhum dos caminhos")
                return None
        
        spec = importlib.util.spec_from_file_location("searchterm", searchterm_path)
        searchterm_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(searchterm_module)
        
        logger.info(f"Módulo searchterm.py carregado com sucesso: {searchterm_path}")
        return searchterm_module
    except Exception as e:
        logger.error(f"Erro ao carregar searchterm.py: {str(e)}")
        return None

# Carrega o módulo na inicialização
searchterm_module = load_searchterm_module()

async def generate_sources_from_content(content: str) -> Optional[str]:
    """
    Gera fontes usando o módulo searchterm baseado no conteúdo da notícia
    """
    try:
        if not searchterm_module:
            logger.error("Módulo searchterm não carregado")
            return None
        
        logger.info(f"Gerando fontes para conteúdo: {len(content)} caracteres")
        
        # Prepara o payload para o searchterm
        payload = {"context": content}
        
        # Chama a função search_terms do módulo searchterm
        # Simula uma requisição FastAPI criando um objeto com o método necessário
        result = await searchterm_module.search_terms(payload)
        
        if result and "file_info" in result:
            file_id = result["file_info"]["file_id"]
            logger.info(f"Fontes geradas com sucesso. File ID: {file_id}")
            logger.info(f"Total de resultados: {result.get('total_results', 0)}")
            logger.info(f"Termos gerados: {len(result.get('generated_terms', []))}")
            
            return file_id
        else:
            logger.error("Resultado inválido do searchterm")
            return None
            
    except Exception as e:
        logger.error(f"Erro ao gerar fontes: {str(e)}")
        return None

def get_brazilian_date_string():
    """
    Retorna a data atual formatada em português brasileiro.
    Implementa fallbacks robustos para diferentes sistemas operacionais.
    """
    try:
        # Tenta configurar o locale brasileiro
        locale_variants = [
            'pt_BR.UTF-8',
            'pt_BR.utf8',
            'pt_BR',
            'Portuguese_Brazil.1252',
            'Portuguese_Brazil',
            'pt_BR.ISO8859-1',
        ]
        
        locale_set = False
        for loc in locale_variants:
            try:
                locale.setlocale(locale.LC_TIME, loc)
                locale_set = True
                break
            except locale.Error:
                continue
        
        if not locale_set:
            locale.setlocale(locale.LC_TIME, '')
        
        now = datetime.now(ZoneInfo("America/Sao_Paulo"))
        
        # Dicionários para tradução manual (fallback)
        meses = {
            1: 'janeiro', 2: 'fevereiro', 3: 'março', 4: 'abril',
            5: 'maio', 6: 'junho', 7: 'julho', 8: 'agosto',
            9: 'setembro', 10: 'outubro', 11: 'novembro', 12: 'dezembro'
        }
        
        dias_semana = {
            0: 'segunda-feira', 1: 'terça-feira', 2: 'quarta-feira', 
            3: 'quinta-feira', 4: 'sexta-feira', 5: 'sábado', 6: 'domingo'
        }
        
        try:
            if locale_set:
                try:
                    date_string = now.strftime("%-d de %B de %Y (%A)")
                except ValueError:
                    try:
                        date_string = now.strftime("%#d de %B de %Y (%A)")
                    except ValueError:
                        date_string = now.strftime("%d de %B de %Y (%A)")
                        if date_string.startswith('0'):
                            date_string = date_string[1:]
                
                date_string = date_string.replace(date_string.split('(')[1].split(')')[0], 
                                                date_string.split('(')[1].split(')')[0].lower())
            else:
                dia = now.day
                mes = meses[now.month]
                ano = now.year
                dia_semana = dias_semana[now.weekday()]
                date_string = f"{dia} de {mes} de {ano} ({dia_semana})"
                
        except Exception:
            dia = now.day
            mes = meses[now.month]
            ano = now.year
            dia_semana = dias_semana[now.weekday()]
            date_string = f"{dia} de {mes} de {ano} ({dia_semana})"
        
        return date_string
        
    except Exception:
        now = datetime.now(ZoneInfo("America/Sao_Paulo"))
        date_string = now.strftime("%d de %B de %Y")
        return date_string

def load_sources_file(file_id: str) -> str:
    """
    Carrega o arquivo de fontes pelo ID do arquivo temporário.
    """
    try:
        # Constrói o caminho do arquivo
        file_path = TEMP_DIR / f"fontes_{file_id}.txt"
        
        # Verifica se o arquivo existe
        if not file_path.exists():
            raise HTTPException(
                status_code=404, 
                detail=f"Arquivo temporário não encontrado ou expirado: {file_id}"
            )
        
        # Lê o conteúdo do arquivo
        with open(file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()
        
        # Se for um JSON, extrai os dados; caso contrário, retorna o conteúdo direto
        try:
            data = json.loads(file_content)
            # Se contém 'results', formata os dados para o Gemini
            if 'results' in data and isinstance(data['results'], list):
                formatted_content = ""
                for idx, result in enumerate(data['results'], 1):
                    formatted_content += f"\n--- FONTE {idx} ---\n"
                    formatted_content += f"Termo: {result.get('term', 'N/A')}\n"
                    formatted_content += f"URL: {result.get('url', 'N/A')}\n"
                    formatted_content += f"Idade: {result.get('age', 'N/A')}\n"
                    formatted_content += f"Conteúdo:\n{result.get('text', 'N/A')}\n"
                    formatted_content += "-" * 50 + "\n"
                return formatted_content
            else:
                return file_content
        except json.JSONDecodeError:
            # Se não for JSON válido, retorna o conteúdo como texto
            return file_content
        
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, 
            detail=f"Arquivo temporário não encontrado: {file_id}"
        )
    except PermissionError:
        raise HTTPException(
            status_code=500, 
            detail=f"Erro de permissão ao acessar arquivo: {file_id}"
        )
    except Exception as e:
        logger.error(f"Erro ao carregar arquivo de fontes {file_id}: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Erro ao carregar arquivo de fontes: {str(e)}"
        )

def extract_text_from_response(response):
    """
    Extrai o texto da resposta de forma robusta com debug.
    """
    logger.info(f"Tipo da resposta: {type(response)}")
    
    # Método 1: Tentar acessar response.text diretamente
    try:
        text_content = getattr(response, 'text', None)
        if text_content:
            logger.info(f"Texto extraído via response.text: {len(text_content)} caracteres")
            return text_content
        else:
            logger.info("response.text existe mas está vazio/None")
    except Exception as e:
        logger.error(f"Erro ao acessar response.text: {e}")
    
    # Método 2: Verificar candidates
    if hasattr(response, 'candidates') and response.candidates:
        logger.info(f"Encontrados {len(response.candidates)} candidates")
        
        for i, candidate in enumerate(response.candidates):
            logger.info(f"Processando candidate {i}")
            
            # Verificar se tem content
            if hasattr(candidate, 'content') and candidate.content:
                content = candidate.content
                logger.info(f"Candidate {i} tem content")
                
                # Verificar se tem parts
                if hasattr(content, 'parts') and content.parts:
                    try:
                        parts_list = list(content.parts)
                        logger.info(f"Content tem {len(parts_list)} parts")
                        
                        response_text = ""
                        for j, part in enumerate(parts_list):
                            logger.info(f"Processando part {j}, tipo: {type(part)}")
                            
                            # Tentar várias formas de acessar o texto
                            part_text = None
                            if hasattr(part, 'text'):
                                part_text = getattr(part, 'text', None)
                            
                            if part_text:
                                logger.info(f"Part {j} tem texto: {len(part_text)} caracteres")
                                response_text += part_text
                            else:
                                logger.info(f"Part {j} não tem texto ou está vazio")
                        
                        if response_text:
                            return response_text
                    except Exception as e:
                        logger.error(f"Erro ao processar parts do candidate {i}: {e}")
    
    return ""

@router.post("/rewrite-news", response_model=NewsResponse)
async def rewrite_news(news: NewsRequest):
    """
    Endpoint para reescrever notícias usando o modelo Gemini.
    Se file_id não for fornecido, gera automaticamente as fontes usando o conteúdo.
    """
    try:
        # Verificar API key
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise HTTPException(status_code=500, detail="API key não configurada")
        
        sources_info = None
        
        # Se file_id não foi fornecido, gera fontes automaticamente
        if not news.file_id:
            logger.info("File ID não fornecido, gerando fontes automaticamente...")
            generated_file_id = await generate_sources_from_content(news.content)
            
            if generated_file_id:
                news.file_id = generated_file_id
                sources_info = {
                    "generated": True,
                    "file_id": generated_file_id,
                    "message": "Fontes geradas automaticamente a partir do conteúdo"
                }
                logger.info(f"Fontes geradas automaticamente. File ID: {generated_file_id}")
            else:
                logger.warning("Não foi possível gerar fontes automaticamente, prosseguindo sem fontes")
                sources_info = {
                    "generated": False,
                    "message": "Não foi possível gerar fontes automaticamente"
                }
        else:
            sources_info = {
                "generated": False,
                "file_id": news.file_id,
                "message": "Usando file_id fornecido"
            }
        
        # Carregar arquivo de fontes se disponível
        sources_content = ""
        if news.file_id:
            try:
                sources_content = load_sources_file(news.file_id)
                logger.info(f"Fontes carregadas: {len(sources_content)} caracteres")
            except HTTPException as e:
                logger.warning(f"Erro ao carregar fontes: {e.detail}")
                sources_content = ""
        
        client = genai.Client(api_key=api_key)
        model = "gemini-2.5-pro"
        
        # Obter data formatada
        date_string = get_brazilian_date_string()
        
        # Instruções do sistema (suas instruções originais aqui)
        # Instruções do sistema
        SYSTEM_INSTRUCTIONS = f"""
Você é um jornalista brasileiro, escrevendo para portais digitais. Sua missão é transformar notícias internacionais em matérias originais, detalhadas e atualizadas para o público brasileiro. Sempre use a notícia-base como ponto de partida, mas consulte o arquivo fontes.txt para extrair todas as informações relevantes, complementando fatos, contexto, dados e antecedentes. Não invente informações; na dúvida, não insira.
Seu estilo de escrita deve ser direto, claro e conversacional, sem jargões ou floreios desnecessários. Frases curtas e bem estruturadas, parágrafos segmentados para leitura digital e SEO. Evite repetições, clichês e generalizações.
Evite frases redundantes ou genéricas como:
- "Destacando como a experiência pode ser um divisor de águas profissional"
- "Reafirma a força criativa do país no cenário global"
- "A revelação contextualizou não apenas sua performance na dança, mas também"
- "A mudança visa estabelecer"
- "Além disso, a consolidação em X trará Y"

O conteúdo deve priorizar clareza, contexto e completude:
- Comece com a informação mais relevante e específica.
- Contextualize causas, consequências e conexões com outros acontecimentos.
- Inclua dados, datas, lançamentos e fontes confiáveis.
- Use citações, títulos de obras e nomes próprios quando pertinentes.
- Finalize sempre com fatos concretos, nunca com opinião genérica.

Nunca utilize as palavras "icônico" ou "icônicos" ao se referir a pessoas, acontecimentos ou objetos neste contexto. O jornal em questão é um veículo de direita, com um público majoritariamente conservador, e esse termo pode soar inadequado ou destoar do tom editorial esperado.
Em vez disso, prefira sempre sinônimos como "lendário", "lendários", "memorável", "histórico" ou outros termos que transmitam grandeza e relevância, mas mantendo a coerência com a linha editorial conservadora.

Formato da matéria:
<headline>título aqui</headline>
<subhead>subtítulo aqui</subhead>
<body>conteúdo aqui</body>

Use <strong> para destaques e <em> para títulos de obras, etc. Também inclua a tag [wiki] para termos que estão presentes na wikipédia. Bote wiki apenas em nomes completos, nomes de filmes, etc... Nunca em apelidos, primeiro nome e tudo mais.
Padrão de títulos: apenas a primeira palavra em maiúscula (exceto nomes próprios ou títulos de obras).

As tags [wiki] devem ser apenas e unicamente para nomes de artistas famosos e eventos conhecidos, exemplo: Segunda Guerra mundial, etc. Não de títulos de filmes ou séries. Além disso, deve ser unicamente no nome da entidade.

CERTO:

O CEO [wiki]Mark Zuckemberg[/wiki] disse em entrevista

ERRADO:

O [wiki]CEO Mark Zuckemberg[/wiki] disse em entrevista

O resultado deve soar como uma reportagem profissional, completa, confiável e envolvente, no estilo da Fox News: informativa, detalhada, mantendo linguagem humana e acessível. Sempre busque complementar a notícia com informações adicionais e contextos do arquivo fontes.txt, evitando opiniões pessoais. Mantenha tom informativo e predominantemente conservador (o jornal tem um público maior da ala da Direita/Republicana, sem juízos de valor ou opiniões pessoais. Não use linguagem woke, de vitimização ou que force interpretações ideológicas.

A data de hoje é {date_string}.

"""

        # Exemplos
        EXAMPLE_INPUT_1 = """
Observação: O Jornal mantém um público majoritário de maioria Republicana americana/Direita brasileira, então é um jornal conservador. Escreva a notícia ciente disso. News base: Ed Helms revealed in an interview that he was nervous about his parents' reaction to the film The Hangover, but in the end everything worked out and her mother loved the movie. The article is out of date, more information is needed.
"""

        EXAMPLE_OUTPUT_1 = """<headline>"Se Beber, Não Case!": Ed Helms, o Dr. Stuart, revela medo do que os pais iriam pensar, mas tudo deu certo</headline>
<subhead>Em uma carreira repleta de surpresas e sucesso internacional, o ator relembra o nervosismo que antecedeu a estreia da comédia que o tornou famoso.</subhead>
<body>
<p>[wiki]<strong>Ed Helms</strong>[/wiki] nunca escondeu o fato de que sua participação em [wiki]<strong>Se Beber, Não Case!</strong>[/wiki] foi um choque cultural, especialmente para seus pais. Em uma entrevista recente ao podcast de [wiki]<strong>Ted Danson</strong>[/wiki], <em>Where Everybody Knows Your Name</em>, o ator falou sobre a ansiedade que sentiu ao imaginar a reação da família à comédia para maiores que o transformou em astro de cinema.</p>
<p>Helms, que foi criado em um lar sulista com valores socialmente conservadores, revelou que, embora o ambiente fosse politicamente progressista, algumas situações, como dentes arrancados, casamentos embriagados e até tigres no banheiro, eram muito diferentes do que seus pais consideravam apropriado. O ator brincou: <em>"Não foi pra isso que me criaram"</em>, fazendo alusão ao enredo caótico do filme de 2009. Ele acrescentou que, embora seus pais já tivessem assistido a algumas de suas performances em programas como <em>The Daily Show</em> e <em>The Office</em>, o que ajudou a criar certa tolerância, o filme ainda o deixava nervoso.</p>
<p>Estrelando sua primeira grande produção, Helms levou os pais para a estreia quando tinha 35 anos. No entanto, foi surpreendido ao ver sua mãe chorando quando as luzes se acenderam. <em>"Pensei: 'Pronto. Acabei de partir o coração da minha mãe'"</em>, recordou. O momento de tensão, porém, durou pouco: ela o tranquilizou dizendo que o filme havia sido hilário.</p>
<p><strong>Se Beber, Não Case!</strong>, dirigido por <strong>Phillips</strong>, foi um sucesso comercial, arrecadando aproximadamente <strong>469 milhões de dólares</strong> em todo o mundo e se tornando a comédia para maiores de classificação indicativa de maior bilheteria até então. A popularidade do filme resultou em duas sequências, lançadas em 2011 e 2013, e consolidou o "bando de lobos" formado por <strong>Helms</strong>, [wiki]<strong>Bradley Cooper</strong>[/wiki] e [wiki]<strong>Zach Galifianakis</strong>[/wiki] como um dos times cômicos mais lendários do cinema moderno.</p>
<p>Sobre a possibilidade de um quarto filme, [wiki]<strong>Bradley Cooper</strong>[/wiki] afirmou em 2023 que toparia participar sem hesitar, principalmente pela chance de reencontrar colegas e diretor. Ainda assim, reconheceu que o projeto é improvável, já que <strong>Phillips</strong> está atualmente focado em empreendimentos de maior escala, como a série de filmes <em>Coringa</em>.</p>
</body>
"""
        EXAMPLE_INPUT_2 = """
Observação: O Jornal mantém um público majoritário de maioria Republicana americana/Direita brasileira, então é um jornal conservador. Escreva a notícia ciente disso. News base: The Office spinoff series 'The Paper' has set a September premiere date at Peacock.
The new mockumentary series from Greg Daniels and Michael Koman will debut Sept. 4 on Peacock, the streamer announced Thursday. The first four episodes of 'The Paper' will premiere on Sept. 4, with two new episodes dropping every Thursday through Sept. 25.
'The Paper' follows the documentary crew that immortalized Dunder Mifflin's Scranton branch in 'The Office' as they find a new subject when they discover a historic Midwestern newspaper and the publisher trying to revive it, according to the official logline.
'The Office' fan-favorite Oscar Nuñez returns to the franchise in 'The Paper,' joining series regulars Domhnall Gleeson, Sabrina Impacciatore, Chelsea Frei, Melvin Gregg, Gbemisola Ikumelo, Alex Edelman, Ramona Young and Tim Key.
Guest stars for the show include Eric Rahill, Tracy Letts, Molly Ephraim, Mo Welch, Allan Havey, Duane Shepard Sr., Nate Jackson and Nancy Lenehan.
'The Paper' was created by Daniels, who created 'The Office,' under his banner Deedle-Dee Productions, and Koman, who has written on 'Nathan for You' and 'SNL.' Produced by Universal Television, a division of Universal Studio Group, 'The Paper' is executive produced by Ricky Gervais, Stephen Merchant, Howard Klein, Ben Silverman and Banijay Americas (formerly Reveille).
Daniels serves as a director on the show alongside Ken Kwapis, Yana Gorskaya, Paul Lieberstein, Tazbah Chavez, Jason Woliner, Jennifer Celotta, Matt Sohn, Dave Rogers and Jeff Blitz.
'The Office' launched in 2005 on NBC and ran for nine seasons leading up to the series finale in 2013. The cast of the beloved sitcom included Steve Carell, Rainn Wilson, John Krasinski, Jenna Fischer, Mindy Kaling and B.J. Novak, among others. The article is out of date, more information is needed.
"""

        EXAMPLE_OUTPUT_2 = """<headline>Nova série do universo 'The Office' ganha título, data de estreia e um rosto familiar</headline>
<subhead>Intitulada 'The Paper', produção de Greg Daniels e Michael Koman chega em setembro com Domhnall Gleeson, Sabrina Impacciatore e o retorno de Oscar Nuñez</subhead>
<body>
<p>A equipe original de documentaristas de <em>"Insane Daily Life at Dunder Mifflin"</em> voltou ao trabalho, desta vez mudando para uma nova história, três anos após o fim de <em>"The Office"</em>. Após uma década de espera, o derivado da amada série de comédia finalmente saiu do papel e será lançado em <strong>4 de setembro de 2025</strong>. O nome do derivado é <em>"The Paper"</em> e estará disponível na plataforma de streaming [wiki]<strong>Peacock</strong>[/wiki].</p>
<p>A trama agora se desloca da fictícia <strong>Scranton, Pensilvânia</strong>, para o escritório de um jornal histórico, porém problemático, localizado no meio-oeste dos Estados Unidos, focando em um jornal em dificuldades na região. A equipe busca uma nova história após cobrir a vida de <strong>Michael Scott</strong> e <strong>Dwight Schrute</strong>. Agora, a equipe acompanha o <strong>Toledo Truth Teller</strong>, um jornal em [wiki]<strong>Toledo, Ohio</strong>[/wiki], e o editor que tenta reviver o jornal com a ajuda de repórteres voluntários.</p>
<p>O novo elenco conta com [wiki]<strong>Domhnall Gleeson</strong>[/wiki], ator irlandês famoso por <em>"Ex Machina"</em> e <em>"Questão de Tempo"</em>, ao lado da atriz italiana [wiki]<strong>Sabrina Impacciatore</strong>[/wiki], que ganhou amplo reconhecimento por seu papel na segunda temporada de <em>"The White Lotus"</em>. Gleeson interpreta o novo editor otimista do jornal, enquanto Impacciatore atua como gerente de redação.</p>
<p>Nas entrevistas mais recentes, Gleeson tenta se distanciar das comparações com o gerente da <strong>Dunder Mifflin</strong>. <em>"Acho que se você tentar competir com o que [wiki]Steve Carell[/wiki] ou [wiki]Ricky Gervais[/wiki] fizeram, seria um enorme erro,"</em> enfatizou o ator, visando construir uma persona totalmente nova. Ele também revelou ter recebido um tipo de conselho de [wiki]<strong>John Krasinski</strong>[/wiki] e até de [wiki]<strong>Steve Carell</strong>[/wiki] para aceitar o papel, especialmente porque se tratava de um projeto de [wiki]<strong>Greg Daniels</strong>[/wiki].</p>
<p>Como <em>"The Paper"</em> está reintroduzindo os personagens originais, os fãs de longa data da série parecem estar encantados, já que também traz [wiki]<strong>Oscar Nuñez</strong>[/wiki] reprisando seu papel como o contador <strong>Oscar Martinez</strong>. Oscar, que estava iniciando uma carreira política em <em>"The Office"</em>, agora parece ter se mudado para <strong>Toledo</strong>. <em>"Eu disse ao Sr. [wiki]<strong>Greg Daniels</strong>[/wiki] que, se Oscar voltasse, ele provavelmente estaria morando em uma cidade mais agitada e cosmopolita. Greg me ouviu e mudou Oscar para [wiki]<strong>Toledo, Ohio</strong>[/wiki], que tem três vezes a população de Scranton. Então, foi bom ser ouvido"</em>, brincou Nuñez durante um evento da [wiki]<strong>NBCUniversal</strong>[/wiki].</p>
<p>[wiki]<strong>Greg Daniels</strong>[/wiki], que anteriormente adaptou <em>"The Office"</em> para o público americano, está em parceria com [wiki]<strong>Michael Koman</strong>[/wiki], cocriador de <em>"Nathan for You"</em>, para este novo projeto. Koman e Daniels, junto com [wiki]<strong>Ricky Gervais</strong>[/wiki] e [wiki]<strong>Stephen Merchant</strong>[/wiki], criadores da série britânica original, formam a equipe de produção executiva.</p>
<p>A primeira temporada de <em>"The Paper"</em> será dividida em <strong>dez episódios</strong>. Nos Estados Unidos, os <strong>quatro primeiros episódios</strong> estarão disponíveis para streaming em <strong>4 de setembro</strong>. Depois disso, os episódios restantes serão lançados no formato de <strong>dois episódios por semana</strong>, com um total de seis episódios liberados até o final em <strong>25 de setembro</strong>.</p>
<p>A série ainda não tem data de estreia confirmada no Brasil, mas a expectativa é de que seja lançada no [wiki]<strong>Universal+</strong>[/wiki], serviço de streaming que costuma exibir produções do catálogo da [wiki]<strong>Peacock</strong>[/wiki].</p>
</body>
"""

        EXAMPLE_INPUT_3 = """
Observação: O Jornal mantém um público majoritário de maioria Republicana americana/Direita brasileira, então é um jornal conservador. Escreva a notícia ciente disso. News base: Noah Centineo Attached to Play Rambo in Prequel Movie 'John Rambo'
"""

        EXAMPLE_OUTPUT_3 = """<headline>Noah Centineo é o novo Rambo em filme que contará a origem do personagem</headline>
<subhead>Ator de 'Para Todos os Garotos que Já Amei' assume o papel de Sylvester Stallone em prelúdio que se passará na Guerra do Vietnã</subhead>
<body>
<p>De acordo com a [wiki]<strong>Millennium Media</strong>[/wiki], [wiki]<strong>Noah Centineo</strong>[/wiki] foi escolhido para interpretar uma versão mais jovem de John Rambo no filme que contará os primórdios do lendário personagem. A produção, que é simplesmente chamada <em>John Rambo</em>, tenta examinar os primeiros anos do soldado antes dos eventos de <em>First Blood</em> (1982).</p>
<p>[wiki]<strong>Jalmari Helander</strong>[/wiki], diretor finlandês mais conhecido pelo blockbuster de ação <em>Sisu</em>, comandará o filme. [wiki]<strong>Rory Haines</strong>[/wiki] e [wiki]<strong>Sohrab Noshirvani</strong>[/wiki], que trabalharam juntos em <em>Black Adam</em>, estão cuidando do roteiro. As filmagens na Tailândia estão previstas para começar no início de 2026.</p>
<p>A história se passará durante a [wiki]Guerra do Vietnã[/wiki], embora os detalhes da trama estejam sendo mantidos em sigilo. O objetivo é retratar a metamorfose de John Rambo. Antes da guerra, ele era "o cara perfeito, o mais popular da escola, um superatleta", como [wiki]<strong>Sylvester Stallone</strong>[/wiki] afirmou em 2019. Espera-se que o filme examine os eventos horríveis que o moldaram no veterano atormentado retratado no primeiro filme.</p>
<p>Embora não esteja diretamente envolvido no projeto, [wiki]<strong>Sylvester Stallone</strong>[/wiki], que interpretou o personagem em cinco filmes, está ciente dele. Segundo pessoas próximas à produção, ele foi informado sobre a escolha de Centineo. O ator, hoje com 79 anos, brincou em 2023 sobre a possibilidade de voltar a interpretar o papel, dizendo: "Ele já fez praticamente tudo. O que eu vou combater? Artrite?"</p>
<p>A escolha de Centineo, de 29 anos, marca uma nova fase na carreira do ator, que conquistou fama internacional com comédias românticas da Netflix, como a trilogia <em>Para Todos os Garotos que Já Amei</em>. Nos últimos anos, porém, ele vem explorando o gênero de ação, interpretando o herói <em>Esmaga-Átomo</em> em <em>Adão Negro</em> e estrelando a série de espionagem <em>O Recruta</em>. Recentemente, Centineo também esteve no drama de guerra <em>Warfare</em>, da [wiki]<strong>A24</strong>[/wiki], e está escalado para viver <strong>Ken Masters</strong> no próximo filme de <em>Street Fighter</em>.</p>
<p>A franquia <em>Rambo</em>, baseada no livro <em>First Blood</em>, de [wiki]<strong>David Morrell</strong>[/wiki], é uma das mais conhecidas do cinema de ação. Os cinco filmes arrecadaram mais de 850 milhões de dólares em todo o mundo. Enquanto as sequências apostaram em ação em grande escala, o primeiro longa se destaca pelo tom mais solene e pela crítica ao tratamento dado aos veteranos do Vietnã.</p>
<p>A produção do novo filme está a cargo de [wiki]<strong>Avi Lerner</strong>[/wiki], [wiki]<strong>Jonathan Yunger</strong>[/wiki], [wiki]<strong>Les Weldon</strong>[/wiki] e [wiki]<strong>Kevin King-Templeton</strong>[/wiki]. A [wiki]<strong>Lionsgate</strong>[/wiki], que distribuiu os dois últimos longas da série, é a principal candidata a adquirir os direitos de distribuição do projeto.</p>
</body>
"""
        EXAMPLE_INPUT_4 = """
Observação: O Jornal mantém um público majoritário de maioria Republicana americana/Direita brasileira, então é um jornal conservador. Escreva a notícia ciente disso. News base: Sylvester Stallone, Gloria Gaynor, Kiss Set for Kennedy Center Honors Amid Trump Overhaul
The first honorees for the revamped Kennedy Center Honors have been unveiled by U.S. President Donald Trump, who is also the new chairman of the John F. Kennedy Center for the Performing Arts.
This year’s honorees include Rocky star and filmmaker Sylvester Stallone; disco-era singer Gloria Gaynor; the rock band Kiss; Michael Crawford, the British star of Phantom of the Opera; and country crooner and songwriter George Strait.
The 48th annual Kennedy Center Honors, set to air on the CBS network and stream on Paramount+, will be hosted by the U.S. President. Stallone was earlier named by Trump as one of his ambassadors to Hollywood. “He’s a very special guy. A real talent, never been given credit for the talent,” Trump added about the Hollywood actor during an hourlong press conference Wednesday
"""

        EXAMPLE_OUTPUT_4 = """<headline>Stallone, Kiss e Gloria Gaynor são os novos homenageados do Kennedy Center em premiação reformulada por Trump</headline>
<subhead>Presidente assume o comando do Kennedy Center, anuncia os homenageados pessoalmente e prioriza foco nas artes em vez de política ideológica</subhead>
<body>
<p>O presidente [wiki]<strong>Donald Trump</strong>[/wiki], agora à frente do conselho do [wiki]<strong>John F. Kennedy Center for the Performing Arts</strong>[/wiki], anunciou pessoalmente os homenageados de 2025 do prestigiado prêmio cultural. Os escolhidos são o ator e cineasta [wiki]<strong>Sylvester Stallone</strong>[/wiki], a banda de rock [wiki]<strong>Kiss</strong>[/wiki], a cantora [wiki]<strong>Gloria Gaynor</strong>[/wiki], o astro da música country [wiki]<strong>George Strait</strong>[/wiki] e o ator britânico [wiki]<strong>Michael Crawford</strong>[/wiki], conhecido por seu papel em <em>O Fantasma da Ópera</em>.</p>
<p>A cerimônia, que será a 48ª edição do evento, será transmitida pela [wiki]CBS[/wiki] e pela plataforma [wiki]<strong>Paramount+</strong>[/wiki] e ocorrerá em [wiki]<strong>Washington, D.C.</strong>[/wiki], no dia 7 de dezembro. Em um evento amplamente divulgado, Trump anunciou os homenageados durante uma coletiva de imprensa na quarta-feira, contrariando o costume de divulgar os nomes por comunicado. Ele afirmou ter se envolvido “cerca de 98%” no processo de seleção e que rejeitou alguns nomes por serem “demasiado woke”.</p>
<p>A alteração, na verdade, representa o início de uma nova fase para o [wiki]<strong>Kennedy Center</strong>[/wiki]. Depois de reassumir a presidência, Trump trocou os indicados de administrações passadas por novos integrantes comprometidos em reorientar o centro. Ele afirmou que seu objetivo é reverter o que considera ser a "programação política 'woke'" e, em tom de brincadeira, sugeriu que poderia receber uma homenagem no ano seguinte.</p>
<p>Trump se absteve de comparecer a qualquer cerimônia no [wiki]<strong>Kennedy Center</strong>[/wiki] durante seu primeiro mandato, depois que vários artistas, incluindo o produtor [wiki]<strong>Norman Lear</strong>[/wiki], ameaçaram boicotar o evento em protesto. Agora, além de supervisionar as escolhas, será o anfitrião da gala.</p>
<p>Os homenageados representam, em certa medida, os interesses pessoais do presidente. [wiki]<strong>Sylvester Stallone</strong>[/wiki], amigo e apoiador de longa data, o descreveu como “um segundo [wiki]George Washington[/wiki]”. Em janeiro, Trump o nomeou, juntamente com [wiki]<strong>Mel Gibson</strong>[/wiki] e [wiki]<strong>Jon Voight</strong>[/wiki], como “embaixador especial” de Hollywood. [wiki]<strong>Michael Crawford</strong>[/wiki] foi o protagonista de <em>O Fantasma da Ópera</em>, um dos musicais preferidos do presidente.</p>
<p>A seleção da banda [wiki]<strong>Kiss</strong>[/wiki] traz um contexto um pouco mais complicado. Embora [wiki]<strong>Ace Frehley</strong>[/wiki], um dos membros fundadores, tenha apoiado Trump, outros integrantes, como [wiki]<strong>Paul Stanley</strong>[/wiki] e [wiki]<strong>Gene Simmons</strong>[/wiki], já expressaram críticas ao presidente em ocasiões anteriores. Simmons, ex-participante do reality show <em>O Aprendiz</em>, declarou em 2022 que Trump "não é republicano nem democrata. Ele está em causa própria."</p>
<p>A nova gestão do [wiki]<strong>Kennedy Center</strong>[/wiki] já provocou respostas no cenário artístico. Os organizadores do musical <em>Hamilton</em> cancelaram a apresentação da turnê nacional no local, e outros artistas, como a atriz [wiki]<strong>Issa Rae</strong>[/wiki] e a produtora [wiki]<strong>Shonda Rhimes</strong>[/wiki], também romperam relações com a instituição em protesto. Em contrapartida, o anúncio dos homenageados gerou tanto interesse que o site oficial do [wiki]<strong>Kennedy Center</strong>[/wiki] ficou temporariamente fora do ar devido ao grande volume de tráfego.</p>
<p>Estabelecido em 1978, o [wiki]<strong>Kennedy Center Honors</strong>[/wiki] possui uma tradição bipartidária, congregando presidentes de diversos partidos para homenagear artistas notáveis de todos os gêneros e estilos.</p>
</body>
"""
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTIONS,
            thinking_config=types.ThinkingConfig(
                thinking_budget=2500,
            ),
            response_mime_type="text/plain",
            max_output_tokens=16000,
            temperature=1,
        )

        # Conteúdo da conversa
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
            # Notícia atual com arquivo de fontes
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=f"News base: {news.content}. The article is out of date, more information is needed."),
                    types.Part.from_text(text=f"Fontes adicionais disponíveis:\n\n{sources_content}")
                ]
            )
        ]

        # Gerar conteúdo
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config
        )

        # Gerar conteúdo
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config
        )

        logger.info("Resposta do modelo recebida com sucesso")

        # Extrair texto
        response_text = extract_text_from_response(response)

        logger.info(f"Texto extraído: {len(response_text) if response_text else 0} caracteres")
        
        # Verificar se o texto está vazio
        if not response_text or response_text.strip() == "":
            logger.error("Texto extraído está vazio")
            raise HTTPException(
                status_code=500, 
                detail="Modelo não retornou conteúdo válido"
            )

        # Extração do título, subtítulo, conteúdo e campos do Instagram
        title_match = re.search(r"<headline>(.*?)</headline>", response_text, re.DOTALL)
        title = title_match.group(1).strip() if title_match else "Título não encontrado"
        
        subhead_match = re.search(r"<subhead>(.*?)</subhead>", response_text, re.DOTALL)
        subhead = subhead_match.group(1).strip() if subhead_match else "Subtítulo não encontrado"
        
        body_match = re.search(r"<body>(.*?)</body>", response_text, re.DOTALL)
        if body_match:
            content = body_match.group(1).strip()
        else:
            body_start_match = re.search(r"<body>(.*)", response_text, re.DOTALL)
            if body_start_match:
                content = body_start_match.group(1).strip()
            else:
                content = "Conteúdo não encontrado"
                
        logger.info(f"Processamento concluído com sucesso - Título: {title[:50]}...")
        
        return NewsResponse(
            title=title,
            subhead=subhead,
            content=content,
            sources_info=sources_info
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro na reescrita: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))