# shared/config.py
from pydantic_settings import BaseSettings
from pydantic import Field
from dataclasses import dataclass, field
from typing import Optional

class KafkaConfig(BaseSettings):
    bootstrap_servers: str = Field(alias="KAFKA_BOOTSTRAP_SERVERS")
    topic_pdf_generated: str = "pdf-generated"
    topic_processing_status: str = "pdf-processing-status"
    topic_dlq: str = "pdf-processing-dlq"
    consumer_group: str = Field(default="pdf-processor-group", alias="KAFKA_CONSUMER_GROUP")


class MinIOConfig(BaseSettings):
    endpoint: str = Field(alias="MINIO_ENDPOINT")
    access_key: str = Field(alias="MINIO_ACCESS_KEY")
    secret_key: str = Field(alias="MINIO_SECRET_KEY")
    bucket: str = Field(default="clinical-trial-pdfs", alias="MINIO_BUCKET")
    secure: bool = False


class PostgresConfig(BaseSettings):
    dsn: str = Field(alias="POSTGRES_DSN")


class QdrantConfig(BaseSettings):
    host: str = Field(default="qdrant", alias="QDRANT_HOST")
    port: int = Field(default=6333, alias="QDRANT_PORT")
    grpc_port: int = Field(default=6334, alias="QDRANT_GRPC_PORT")
    collection_name: str = "clinical_trial_embeddings"


class Neo4jConfig(BaseSettings):
    uri: str = Field(alias="NEO4J_URI")
    user: str = Field(alias="NEO4J_USER")
    password: str = Field(alias="NEO4J_PASSWORD")


class OpenAIConfig(BaseSettings):
    api_key: str = Field(alias="OPENAI_API_KEY")
    embedding_model: str = "text-embedding-3-large"
    embedding_dimensions: int = 3072
    llm_model: str = "gpt-4o"


class AppConfig(BaseSettings):
    kafka: KafkaConfig = KafkaConfig()
    minio: MinIOConfig = MinIOConfig()
    postgres: PostgresConfig = PostgresConfig()
    qdrant: QdrantConfig = QdrantConfig()
    neo4j: Neo4jConfig = Neo4jConfig()
    openai: OpenAIConfig = OpenAIConfig()

@dataclass
class KeycloakConfig:
    url: str = "http://keycloak:8180"
    realm: str = "clinical-trials"
    client_id: str = "research-platform-api"
    client_secret: str = "research-platform-secret"

    @property
    def issuer_url(self) -> str:
        return f"{self.url}/realms/{self.realm}"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer_url}/protocol/openid-connect/certs"

    @property
    def token_url(self) -> str:
        return f"{self.issuer_url}/protocol/openid-connect/token"

    @property
    def userinfo_url(self) -> str:
        return f"{self.issuer_url}/protocol/openid-connect/userinfo"


@dataclass
class OpenFGAConfig:
    api_url: str = "http://openfga:8080"
    store_id: str = ""
    authorization_model_id: Optional[str] = None
    # Fail closed — deny all if OpenFGA is unreachable
    fail_closed: bool = True
    # Timeout for authorization checks
    check_timeout_seconds: float = 2.0


@dataclass
class AuthorizationConfig:
    # K-anonymity minimum group size for aggregate queries
    k_anonymity_min_group_size: int = 5
    # Maximum number of trials returned in a single query
    max_trials_per_query: int = 50
    # Default access grant duration (days)
    default_grant_duration_days: int = 365
    # Aggregate-only SQL forbidden patterns
    forbidden_sql_patterns: list = field(default_factory=lambda: [
        "patient_id", "subject_id", "patient.age", "date_of_birth",
        "LIMIT 1", "FETCH FIRST 1"
    ])