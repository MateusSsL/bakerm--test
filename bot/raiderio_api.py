import aiohttp
import re

async def obter_score_raiderio(url: str) -> tuple:
    """
    Obtém informações do personagem no Raider.IO
    Retorna (score, classe) ou (None, None) se erro
    """
    try:
        # Extrai região/reino/nome do URL
        pattern = r"characters/(\w+)/([^/]+)/([^/]+)"
        match = re.search(pattern, url)
        if not match:
            return None, None
            
        region, realm, name = match.groups()
        
        # URL da API do Raider.IO
        api_url = f"https://raider.io/api/v1/characters/profile"
        params = {
            "region": region,
            "realm": realm,
            "name": name,
            "fields": "mythic_plus_scores_by_season:current,class"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, params=params) as response:
                if response.status != 200:
                    return None, None
                    
                data = await response.json()
                
                # Pega score da season atual
                current_score = data.get("mythic_plus_scores_by_season", [{}])[0].get("scores", {}).get("all", 0)
                class_name = data.get("class")
                
                return float(current_score), class_name
                
    except Exception as e:
        print(f"[ERRO RAIDERIO] {e}")
        return None, None