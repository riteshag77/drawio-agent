const DRAWIO_ORIGIN = 'https://embed.diagrams.net';
const DRAWIO_THEMES = ['kennedy', 'atlas', 'dark', 'min', 'sketch', 'simple'];
const THEME_STORAGE_KEY = 'drawio-agent-drawio-theme';
const TEMPLATES_STORAGE_KEY = 'drawio-agent-templates';

const VERBOSE_STORAGE_KEY = 'drawio-agent-verbose-logging';
const MINIMAL_STYLE_STORAGE_KEY = 'drawio-agent-minimal-style';
const CUSTOM_SYSTEM_MESSAGE_KEY = 'drawio-agent-custom-system-message';
const VLM_VALIDATION_KEY = 'drawio-agent-vlm-validation';
const ERROR_PANEL_KEY = 'drawio-agent-error-panel';
const VALIDATION_PROVIDER_KEY = 'drawio-agent-validation-provider';
const AUTO_LAYOUT_STORAGE_KEY = 'drawio-agent-autolayout-on-load';
const DIAGRAM_MODE_STORAGE_KEY = 'drawio-agent-diagram-mode';
const MAX_VLM_CLIENT_RETRIES = 0; // Show banner only — auto /chat regen feels like endless loading

let currentXml = '';
let previousXml = null;
let currentArchitecture = null;
let previousArchitecture = null;
let pendingXmlExport = null;
let attachmentContext = '';
let attachmentImageData = null;
let hasImageAttachment = false;
let lastValidationFeedback = null;
let pendingPngExport = null;
let chatMessages = [];
let appliedSuggestionTexts = [];
let isSending = false;
let chatAbortController = null;
let runStoppedByUser = false;
let selectedPlatform = 'aws';
let drawioReady = false;
let pendingXml = null;
let pendingMermaid = null;
let currentMermaid = '';
let messageHandler = null;
let verboseLogging = false;
let validationPanelActive = false;
let errorPanelActive = false;
let logEntryCount = 0;
let vlmAutoRetryCount = 0;

const EXAMPLE_PROMPTS = {
    aws: [
        'Highly available web app with API Gateway, Fargate in public/private subnets, Aurora, ElastiCache, CloudFront, Route 53, WAF, and CloudWatch',
        'Serverless data pipeline: S3 → Lambda → SQS → ECS workers → Redshift with KMS encryption',
    ],
    gcp: [
        'Cloud Run in Shared VPC with Cloud SQL, Memorystore, Cloud CDN, Cloud DNS, Cloud Armor, and Cloud Monitoring',
        'GKE cluster with private nodes, Cloud Load Balancing, Pub/Sub event bus, and BigQuery analytics',
    ],
    azure: [
        'Application Gateway, AKS in VNet subnets, Cosmos DB, Redis, Front Door, and Azure Monitor',
        'Event-driven Azure Functions with Service Bus, Blob Storage, and Key Vault secrets',
    ],
    multi: [
        'Hybrid: AWS ECS tasks querying GCP Spanner over site-to-site VPN with centralized logging',
        'Multi-cloud DR: primary AWS Aurora with Azure Cosmos DB read replica and global traffic manager',
    ],
};

const CHAT_PLACEHOLDERS = {
    aws: 'e.g., Add ElastiCache between API and database, or describe a new AWS architecture…',
    gcp: 'e.g., Switch Cloud Run to GKE, or describe a new GCP architecture…',
    azure: 'e.g., Add Azure Front Door WAF, or describe a new Azure architecture…',
    multi: 'e.g., Add cross-cloud VPN, or describe a hybrid architecture…',
};

const GALLERY_PATTERNS = [
    {
        id: 'three_tier',
        title: '3-Tier Web App',
        desc: 'Load-balanced web, app, and database tiers with CDN, caching, and HA across AZs',
        platforms: ['aws', 'gcp', 'azure'],
        prompt: 'Highly available web app with load balancer, app servers in private subnets across multiple AZs, managed database with read replicas, CDN for static assets, cache layer, WAF, DNS routing, and CloudWatch monitoring',
    },
    {
        id: 'serverless',
        title: 'Serverless API',
        desc: 'API Gateway, functions, NoSQL database, object storage, and managed monitoring',
        platforms: ['aws', 'gcp', 'azure'],
        prompt: 'Serverless API with API Gateway, Lambda functions, NoSQL database, object storage, message queue, secrets manager, and monitoring dashboard',
    },
    {
        id: 'event_driven',
        title: 'Event-Driven',
        desc: 'Async processing with queues, topics, event buses, and worker compute',
        platforms: ['aws', 'gcp', 'azure'],
        prompt: 'Event-driven architecture with event bus, message queue, topic/subscriptions, stream processor, worker services in private subnets, dead-letter queue, and observability',
    },
    {
        id: 'data_analytics',
        title: 'Data Analytics',
        desc: 'Ingestion, object storage, ETL pipeline, warehouse, and BI dashboard',
        platforms: ['aws', 'gcp', 'azure'],
        prompt: 'Data analytics pipeline with ingestion API, object storage landing zone, ETL processing, data warehouse, BI analytics, catalog, and orchestration',
    },
    {
        id: 'k8s_microservices',
        title: 'Microservices (K8s)',
        desc: 'Container orchestration with ingress, service mesh, registry, and observability',
        platforms: ['aws', 'gcp', 'azure', 'multi'],
        prompt: 'Kubernetes cluster with ingress controller, service mesh, container registry, multiple microservices, managed database, message queue, secrets, and Prometheus monitoring',
    },
    {
        id: 'static_site',
        title: 'Static Site + CDN',
        desc: 'Global content delivery with object storage, serverless API, DNS, and WAF',
        platforms: ['aws', 'gcp', 'azure', 'multi'],
        prompt: 'Static site hosted on object storage with global CDN, DNS management, WAF, serverless API backend, CI/CD pipeline, and monitoring',
    },
];

const GALLERY_ICONS = {
    three_tier: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="8" height="5" rx="1"/><rect x="14" y="2" width="8" height="5" rx="1"/><rect x="8" y="9.5" width="8" height="5" rx="1"/><rect x="2" y="17" width="8" height="5" rx="1"/><rect x="14" y="17" width="8" height="5" rx="1"/><path d="M6 7v2.5M18 7v2.5M12 14.5V17M6 12v5M18 12v5"/></svg>',
    serverless: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/><circle cx="12" cy="12" r="10" stroke-dasharray="3 3"/></svg>',
    event_driven: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="12" r="3"/><circle cx="19" cy="12" r="3"/><circle cx="12" cy="5" r="2.5"/><circle cx="12" cy="19" r="2.5"/><path d="M7.5 10.5L9.5 7M16.5 10.5L14.5 7M7.5 13.5L9.5 17M16.5 13.5L14.5 17"/></svg>',
    data_analytics: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12v6"/><path d="M12 15v3"/><path d="M7 14v4"/></svg>',
    k8s_microservices: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="18" r="2"/><path d="M7 7l3 3M14 7l3 3M7 17l3-3M14 17l3-3"/></svg>',
    static_site: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/><rect x="9" y="7" width="6" height="4" rx="1"/><path d="M9 15h6M10 13v2M14 13v2"/></svg>',
};

const PATTERN_LABELS = {
    three_tier_web: 'HA 3-Tier Web',
    serverless_api: 'Serverless API',
    event_driven: 'Event-Driven',
    data_analytics: 'Data Analytics',
    microservices_k8s: 'Microservices',
    static_site_cdn: 'Static Site + CDN',
};
