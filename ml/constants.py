"""
ml/constants.py

Single source of truth for all ML pipeline configuration.
Changing a constant here propagates to every module that imports it.
"""

from __future__ import annotations

# ============================================================================
# SKILL NORMALIZATION MAP
# Aliases → canonical skill name.
# Applied after extraction so "py" and "Python" both become "python".
# Keys must be lowercase (input is lowercased before lookup).
# ============================================================================
SKILL_ALIASES: dict[str, str] = {
    # Languages
    "py":           "python",
    "python3":      "python",
    "js":           "javascript",
    "ts":           "typescript",
    "nodejs":       "node.js",
    "node":         "node.js",
    "node js":      "node.js",
    "golang":       "go",
    "c sharp":      "c#",
    "csharp":       "c#",
    "cpp":          "c++",
    "c plus plus":  "c++",
    "rust lang":    "rust",
    "rb":           "ruby",
    "kotlin jvm":   "kotlin",
    "objective c":  "objective-c",
    "objc":         "objective-c",
    "shell":        "bash",
    "bash shell":   "bash",
    "sh":           "bash",
    "r lang":       "r",
    "r language":   "r",
    "scala lang":   "scala",
    "elixir lang":  "elixir",
    "haskell lang": "haskell",

    # Web frameworks
    "react.js":     "react",
    "reactjs":      "react",
    "react js":     "react",
    "vuejs":        "vue.js",
    "vue js":       "vue.js",
    "vue":          "vue.js",
    "angular js":   "angular",
    "angularjs":    "angular",
    "nextjs":       "next.js",
    "next js":      "next.js",
    "nuxtjs":       "nuxt.js",
    "nuxt js":      "nuxt.js",
    "sveltejs":     "svelte",
    "svelte js":    "svelte",
    "django rest":  "django",
    "flask api":    "flask",
    "fastapi":      "fastapi",
    "springboot":   "spring boot",
    "spring mvc":   "spring boot",
    "rails":        "ruby on rails",
    "ror":          "ruby on rails",
    "laravel php":  "laravel",
    "express":      "express.js",
    "expressjs":    "express.js",
    "express js":   "express.js",

    # Databases
    "postgres":     "postgresql",
    "pg":           "postgresql",
    "psql":         "postgresql",
    "mysql db":     "mysql",
    "mssql":        "sql server",
    "ms sql":       "sql server",
    "mongo":        "mongodb",
    "mongo db":     "mongodb",
    "redis cache":  "redis",
    "elastic":      "elasticsearch",
    "es":           "elasticsearch",
    "opensearch":   "elasticsearch",
    "dynamo":       "dynamodb",
    "dynamo db":    "dynamodb",
    "cassandra db": "cassandra",
    "cockroach":    "cockroachdb",
    "couch":        "couchdb",
    "neo4j graph":  "neo4j",
    "sqlite3":      "sqlite",

    # Cloud
    "aws cloud":    "aws",
    "amazon aws":   "aws",
    "amazon web services": "aws",
    "gcp":          "google cloud",
    "google gcp":   "google cloud",
    "azure cloud":  "azure",
    "ms azure":     "azure",
    "microsoft azure": "azure",
    "ali cloud":    "alibaba cloud",

    # DevOps / Infra
    "k8s":          "kubernetes",
    "kube":         "kubernetes",
    "k8":           "kubernetes",
    "docker container": "docker",
    "dockerfile":   "docker",
    "tf":           "terraform",
    "github actions": "github actions",
    "gh actions":   "github actions",
    "jenkins ci":   "jenkins",
    "gitlab ci":    "gitlab ci/cd",
    "ansible automation": "ansible",
    "puppet automation": "puppet",

    # ML / Data
    "sklearn":      "scikit-learn",
    "sci-kit learn": "scikit-learn",
    "scikit learn": "scikit-learn",
    "tf":           "tensorflow",
    "tensorflow2":  "tensorflow",
    "torch":        "pytorch",
    "pytorch lightning": "pytorch",
    "hf":           "hugging face",
    "huggingface":  "hugging face",
    "bert nlp":     "bert",
    "gpt api":      "openai api",
    "llm":          "large language models",
    "llms":         "large language models",
    "gen ai":       "generative ai",
    "genai":        "generative ai",
    "langchain framework": "langchain",
    "pandas df":    "pandas",
    "np":           "numpy",
    "seaborn plots": "seaborn",
    "tableau dashboard": "tableau",
    "powerbi":      "power bi",
    "power-bi":     "power bi",
    "looker studio": "looker",
    "apache spark": "spark",
    "pyspark":      "spark",
    "apache kafka": "kafka",
    "apache airflow": "airflow",
    "dbt core":     "dbt",

    # Mobile
    "react-native": "react native",
    "rn":           "react native",
    "flutter dart": "flutter",
    "ios swift":    "swift",
    "android kotlin": "kotlin",

    # General
    "rest api":     "rest apis",
    "restful":      "rest apis",
    "restful api":  "rest apis",
    "graphql api":  "graphql",
    "grpc":         "grpc",
    "micro services": "microservices",
    "micro-services": "microservices",
    "agile scrum":  "agile",
    "scrum agile":  "agile",
    "ci cd":        "ci/cd",
    "ci/cd pipeline": "ci/cd",
    "cicd":         "ci/cd",
    "tdd":          "test-driven development",
    "ddd":          "domain-driven design",
    "oop":          "object-oriented programming",
    "fp":           "functional programming",
    "oauth2":       "oauth",
    "oauth 2":      "oauth",
    "jwt token":    "jwt",
    "json web token": "jwt",
    "ssl tls":      "ssl/tls",
    "linux unix":   "linux",
    "unix linux":   "linux",
    "git github":   "git",
    "github git":   "git",
    "jira project": "jira",
    "confluence docs": "confluence",
}

# ============================================================================
# SKILL CATEGORIES
# Maps canonical skill names to their logical grouping.
# Used to populate SkillTrend.skill_category.
# ============================================================================
SKILL_CATEGORIES: dict[str, str] = {
    # Languages
    "python": "language", "javascript": "language", "typescript": "language",
    "java": "language", "go": "language", "rust": "language", "c++": "language",
    "c#": "language", "ruby": "language", "php": "language", "swift": "language",
    "kotlin": "language", "scala": "language", "r": "language", "elixir": "language",
    "dart": "language", "haskell": "language", "clojure": "language",
    "objective-c": "language", "bash": "language", "perl": "language",
    "lua": "language", "julia": "language", "groovy": "language",

    # Web frameworks
    "react": "framework", "vue.js": "framework", "angular": "framework",
    "next.js": "framework", "nuxt.js": "framework", "svelte": "framework",
    "django": "framework", "flask": "framework", "fastapi": "framework",
    "spring boot": "framework", "ruby on rails": "framework",
    "express.js": "framework", "laravel": "framework", "nest.js": "framework",
    "node.js": "runtime", "deno": "runtime",

    # Databases
    "postgresql": "database", "mysql": "database", "mongodb": "database",
    "redis": "database", "elasticsearch": "database", "sqlite": "database",
    "dynamodb": "database", "cassandra": "database", "cockroachdb": "database",
    "neo4j": "database", "couchdb": "database", "mariadb": "database",
    "sql server": "database", "oracle": "database", "snowflake": "database",
    "bigquery": "database", "clickhouse": "database",

    # Cloud
    "aws": "cloud", "google cloud": "cloud", "azure": "cloud",
    "alibaba cloud": "cloud", "digitalocean": "cloud", "heroku": "cloud",
    "vercel": "cloud", "netlify": "cloud",

    # DevOps
    "docker": "devops", "kubernetes": "devops", "terraform": "devops",
    "ansible": "devops", "jenkins": "devops", "github actions": "devops",
    "gitlab ci/cd": "devops", "ci/cd": "devops", "helm": "devops",
    "prometheus": "devops", "grafana": "devops", "datadog": "devops",
    "nginx": "devops", "linux": "devops", "git": "devops",

    # ML / AI / Data
    "scikit-learn": "ml", "tensorflow": "ml", "pytorch": "ml",
    "keras": "ml", "xgboost": "ml", "lightgbm": "ml",
    "hugging face": "ml", "bert": "ml", "openai api": "ml",
    "large language models": "ml", "generative ai": "ml",
    "langchain": "ml", "llamaindex": "ml", "rag": "ml",
    "pandas": "data", "numpy": "data", "spark": "data",
    "kafka": "data", "airflow": "data", "dbt": "data",
    "tableau": "data", "power bi": "data", "looker": "data",
    "matplotlib": "data", "seaborn": "data", "plotly": "data",

    # Mobile
    "react native": "mobile", "flutter": "mobile", "swift": "mobile",
    "kotlin": "mobile", "ionic": "mobile", "xamarin": "mobile",

    # Architecture
    "microservices": "architecture", "rest apis": "architecture",
    "graphql": "architecture", "grpc": "architecture",
    "event-driven": "architecture", "domain-driven design": "architecture",

    # Testing
    "pytest": "testing", "jest": "testing", "cypress": "testing",
    "selenium": "testing", "test-driven development": "testing",

    # Tools
    "jira": "tool", "confluence": "tool", "figma": "tool",
    "postman": "tool", "swagger": "tool",
}

# ============================================================================
# NOISE WORDS
# Tokens to discard during NLP extraction.
# These appear in job descriptions but are not skills.
# ============================================================================
NOISE_WORDS: frozenset[str] = frozenset({
    # Generic job description language
    "experience", "years", "knowledge", "understanding", "familiarity",
    "ability", "skills", "skill", "work", "working", "team", "company",
    "position", "role", "job", "opportunity", "candidate", "applicant",
    "required", "requirements", "preferred", "plus", "bonus", "nice",
    "strong", "solid", "good", "excellent", "proficiency", "proficient",
    "hands", "on", "familiar", "comfort", "comfortable", "looking",
    "seeking", "hiring", "join", "us", "our", "we", "you", "your",
    "including", "such", "other", "related", "relevant", "various",
    "etc", "eg", "example", "e.g", "i.e",

    # Generic tech words that are not themselves skills
    "software", "development", "engineering", "developer", "engineer",
    "system", "systems", "application", "applications", "service",
    "services", "platform", "solution", "solutions", "product",
    "products", "tool", "tools", "technology", "technologies", "tech",
    "framework", "frameworks", "library", "libraries", "stack",
    "environment", "infrastructure", "architecture", "design",
    "implementation", "integration", "deployment", "production",
    "backend", "frontend", "fullstack", "full-stack", "full stack",
    "web", "mobile", "cloud", "data", "code", "coding", "programming",

    # Remote / location noise from tags
    "remote", "worldwide", "global", "anywhere", "digital nomad",
    "non tech", "non-tech", "adult", "exec", "operations", "operational",
    "finance", "legal", "medical", "hr", "strategy", "sales",
    "marketing", "recruiting", "management",

    # Numbers and punctuation that slip through
    "2", "3", "4", "5", "10", "year", "month",
})

# ============================================================================
# KNOWN SKILL TOKENS
# A curated list of tokens that are definitively skills.
# The NLP extractor uses this as a high-confidence lookup before
# attempting pattern matching on the full description.
# Lowercase only.
# ============================================================================
KNOWN_SKILLS: frozenset[str] = frozenset({
    # Languages
    "python", "javascript", "typescript", "java", "go", "rust", "c++",
    "c#", "ruby", "php", "swift", "kotlin", "scala", "r", "elixir",
    "dart", "haskell", "clojure", "objective-c", "bash", "perl", "lua",
    "julia", "groovy", "cobol", "fortran", "matlab", "erlang", "ocaml",
    "f#", "nim", "zig", "crystal", "d",

    # Web
    "react", "vue.js", "angular", "next.js", "nuxt.js", "svelte",
    "django", "flask", "fastapi", "spring boot", "ruby on rails",
    "express.js", "laravel", "nest.js", "gatsby", "remix", "astro",
    "tailwind", "bootstrap", "sass", "css", "html", "html5", "css3",
    "webpack", "vite", "babel", "eslint", "prettier",

    # Runtime / server
    "node.js", "deno", "bun",

    # Database
    "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
    "sqlite", "dynamodb", "cassandra", "cockroachdb", "neo4j",
    "couchdb", "mariadb", "sql server", "oracle", "snowflake",
    "bigquery", "clickhouse", "supabase", "planetscale", "neon",
    "influxdb", "timescaledb", "pinecone", "weaviate", "qdrant",

    # Cloud
    "aws", "google cloud", "azure", "digitalocean", "heroku",
    "vercel", "netlify", "cloudflare", "fly.io", "railway",

    # DevOps
    "docker", "kubernetes", "terraform", "ansible", "jenkins",
    "github actions", "gitlab ci/cd", "helm", "prometheus",
    "grafana", "datadog", "nginx", "linux", "git", "pulumi",
    "argocd", "istio", "envoy", "vault",

    # ML / AI
    "scikit-learn", "tensorflow", "pytorch", "keras", "xgboost",
    "lightgbm", "catboost", "hugging face", "bert", "openai api",
    "langchain", "llamaindex", "rag", "llm", "generative ai",
    "stable diffusion", "whisper", "spacy", "nltk", "gensim",
    "mlflow", "weights & biases", "dvc", "bentoml", "triton",

    # Data
    "pandas", "numpy", "spark", "kafka", "airflow", "dbt",
    "tableau", "power bi", "looker", "matplotlib", "seaborn",
    "plotly", "dask", "ray", "flink", "beam", "dagster",
    "prefect", "great expectations", "dlt", "polars",

    # Mobile
    "react native", "flutter", "ionic", "xamarin",

    # Architecture
    "microservices", "rest apis", "graphql", "grpc", "websockets",
    "rabbitmq", "nats", "celery", "rq",

    # Testing
    "pytest", "jest", "cypress", "selenium", "playwright",
    "test-driven development", "vitest", "junit", "mockito",

    # Security
    "oauth", "jwt", "ssl/tls", "sso", "saml", "ldap",

    # Tools
    "git", "jira", "confluence", "figma", "postman", "swagger",
    "openapi", "linux", "vim", "vscode",

    # Methodologies
    "agile", "scrum", "kanban", "devops", "sre", "ci/cd",
    "object-oriented programming", "functional programming",
    "domain-driven design", "test-driven development",
})

# ============================================================================
# TREND THRESHOLDS
# ============================================================================
# Minimum job count before we compute a trend for a skill.
# Below this, variance is too high to produce meaningful direction signals.
MIN_JOBS_FOR_TREND: int = 3

# Percentage change thresholds for trend direction classification
TREND_RISING_THRESHOLD:   float = 10.0   # +10% or more = RISING
TREND_DECLINING_THRESHOLD: float = -10.0  # -10% or less = DECLINING
# Between -10% and +10% = STABLE
# No prior period data = NEW

# Minimum confidence to assign a trend direction (below = NEW)
MIN_TREND_CONFIDENCE: float = 0.3

# ============================================================================
# SALARY CONSTANTS
# ============================================================================
# Jobs with salary below this are likely hourly/contract rates mis-classified
# as annual — treat as outliers and exclude from aggregations
SALARY_MIN_ANNUAL_USD: int = 15_000
SALARY_MAX_ANNUAL_USD: int = 1_000_000  # Above this is likely a data error

# Supported currencies (others are logged and skipped in aggregations)
SUPPORTED_CURRENCIES: frozenset[str] = frozenset({"USD", "EUR", "GBP", "INR", "CAD", "AUD"})

# Approximate USD conversion rates for cross-currency comparison
# In production, replace with a live exchange rate API call
USD_CONVERSION_RATES: dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.09,
    "GBP": 1.27,
    "INR": 0.012,
    "CAD": 0.74,
    "AUD": 0.65,
}

# ============================================================================
# BATCH SIZES
# ============================================================================
SKILL_EXTRACTION_BATCH_SIZE: int = 50   # Jobs per extraction batch
TREND_COMPUTATION_BATCH_SIZE: int = 500  # Jobs loaded per trend computation pass
