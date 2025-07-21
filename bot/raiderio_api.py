import aiohttp
from typing import Optional

async def obter_score_raiderio(url: str) -> Optional[float]:
    """Obtém score M+ atual da API do Raider.IO"""
    try:
        if "raider.io/characters/" not in url:
            return None
            
        parts = url.rstrip("/").split("/")
        if len(parts) < 3:
            return None
            
        region, realm, name = parts[-3], parts[-2], parts[-1]
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://raider.io/api/v1/characters/profile"
                f"?region={region}&realm={realm}&name={name}"
                f"&fields=mythic_plus_scores_by_season:current"
            ) as resp:
                if resp.status != 200:
                    return None
                
                data = await resp.json()
                return data["mythic_plus_scores_by_season"][0]["scores"]["all"]
                
    except (aiohttp.ClientError, KeyError, IndexError, ValueError) as e:
        print(f"[Raider.IO API Error] {e}")
        return None