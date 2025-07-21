import aiohttp
import re

async def obter_score_raiderio(url: str) -> tuple:
    """
    Obtém informações do personagem no Raider.IO
    Retorna (score, classe, server) ou (None, None, None) se erro
    """
    try:
        # Extrai região, reino e nome do URL
        pattern = r"characters/(\w+)/([^/]+)/([^/]+)"
        match = re.search(pattern, url)
        if not match:
            return None, None, None

        region, realm, name = match.groups()
        # Realm pode vir com hífen, padronize para o formato correto
        realm_api = realm.replace("-", " ").title()

        # URL da API do Raider.IO
        api_url = "https://raider.io/api/v1/characters/profile"
        params = {
            "region": region,
            "realm": realm,
            "name": name,
            "fields": "mythic_plus_scores_by_season:current,class"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, params=params) as response:
                if response.status != 200:
                    return None, None, None

                data = await response.json()

                # Score da season atual
                scores_season = data.get("mythic_plus_scores_by_season", [])
                current_score = 0
                if scores_season and "scores" in scores_season[0]:
                    current_score = scores_season[0]["scores"].get("all", 0)
                class_name = data.get("class")
                realm_name = data.get("realm", realm_api)  # Realm pode vir da API ou do link

                return float(current_score), class_name, realm_name

    except Exception as e:
        print(f"[ERRO RAIDERIO] {e}")
        return None, None, None