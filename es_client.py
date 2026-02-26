import os
from elasticsearch import Elasticsearch
from dotenv import load_dotenv

load_dotenv()

_client: Elasticsearch | None = None


def get_client() -> Elasticsearch:
    global _client
    if _client is None:
        host    = os.getenv("ES_HOST", "http://localhost:9200")
        api_key = os.getenv("ES_API_KEY", "")

        if api_key:
            # Elastic Cloud Serverless — API key auth
            _client = Elasticsearch(
                hosts=[host],
                api_key=api_key,
                request_timeout=30,
            )
        else:
            # Local Docker — basic auth
            _client = Elasticsearch(
                hosts=[host],
                basic_auth=(
                    os.getenv("ES_USERNAME", "elastic"),
                    os.getenv("ES_PASSWORD", "mars_hackathon"),
                ),
                request_timeout=30,
            )
    return _client


def check_connection() -> bool:
    try:
        info = get_client().info()
        print(f"✅  Connected to Elasticsearch {info['version']['number']}")
        return True
    except Exception as e:
        print(f"❌  Elasticsearch connection failed: {e}")
        return False