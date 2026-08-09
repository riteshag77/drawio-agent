const DRAWIO_ORIGIN = 'https://embed.diagrams.net';
const DRAWIO_THEMES = ['kennedy', 'atlas', 'dark', 'min', 'sketch', 'simple'];
const THEME_STORAGE_KEY = 'drawio-agent-drawio-theme';
const APP_THEMES = ['midnight-slate', 'daylight', 'aurora-contrast'];
const APP_THEME_KEY = 'drawio-agent-app-theme';
const APP_THEME_TO_DRAWIO_THEME = {
    'midnight-slate': 'kennedy',
    'daylight': 'kennedy',
    'aurora-contrast': 'dark',
};
const VERBOSE_STORAGE_KEY = 'drawio-agent-verbose-logging';
const CUSTOM_SYSTEM_MESSAGE_KEY = 'drawio-agent-custom-system-message';
const VLM_VALIDATION_KEY = 'drawio-agent-vlm-validation';
const VALIDATION_PROVIDER_KEY = 'drawio-agent-validation-provider';
const COST_ESTIMATE_STORAGE_KEY = 'drawio-agent-cost-estimate';
const WELL_ARCHITECTED_KEY = 'drawio-agent-well-architected';
const COMPLIANCE_KEY = 'drawio-agent-compliance';
const GOVERNANCE_TOGGLE_KEY = 'drawio-agent-governance-tab';
const COMPLIANCE_FRAMEWORKS_KEY = 'drawio-agent-compliance-frameworks';
const DIAGRAM_MODE_STORAGE_KEY = 'drawio-agent-diagram-mode';
const LIBAVOID_STORAGE_KEY = 'drawio-agent-libavoid';
const IMAGE_MODE_STORAGE_KEY = 'drawio-agent-image-mode';
const ANIMATED_CONNECTORS_STORAGE_KEY = 'drawio-agent-animated-connectors';
const THREE_D_STORAGE_KEY = 'drawio-agent-three-d';
const SHOW_REASONING_STORAGE_KEY = 'drawio-agent-show-reasoning';
const SUGGESTIONS_STORAGE_KEY = 'drawio-agent-suggestions';
const ARCH_INTERVIEW_STORAGE_KEY = 'drawio-agent-architecture-interview';
const DIAGRAM_VIEWS_STORAGE_KEY = 'drawio-agent-diagram-views';
const ADR_NOTES_STORAGE_KEY = 'drawio-agent-adr-notes';
const RAID_REGISTER_STORAGE_KEY = 'drawio-agent-raid-register';
const SAVE_CAPTURES_STORAGE_KEY = 'drawio-agent-save-captures';
const CUSTOM_PROVIDERS_STORAGE_KEY = 'drawio-agent-custom-providers'; // [{id,label,client,api_key,base_url,model}] — browser-only, sent per-request
const MAX_VLM_AUTO_RETRIES = 3;
const SIDEBAR_COLLAPSED_KEY = 'drawio-agent-sidebar-collapsed';
const SIDEBAR_WIDTH_KEY = 'drawio-agent-sidebar-width';
const MOBILE_PANE_KEY = 'drawio-agent-mobile-pane';
const SESSION_ID_KEY = 'drawio-agent-session-id';

/** Pending interview answers for the next generate (cleared after successful diagram). */
let pendingInterviewAnswers = null;

let currentXml = '';
let previousXml = null;
let currentArchitecture = null;
let previousArchitecture = null;
let pendingXmlExport = null;
let attachmentContext = '';
let attachmentDocuments = []; // [{filename, mime, data}] — vision-fallback PDFs/images, sent alongside context_text
let attachmentDiagramXml = null; // validated .drawio/.xml from paperclip — sent as current_xml
let attachments = []; // [{filename, status: 'loading'|'ready'|'error', message, truncated}] — chip tracking
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
let currentDrawioPageIndex = 0; // reset to 0 on every full 'load' — draw.io always opens page 1
let pendingFocusPageIndex = null; // set before a load whose response should land on a specific tab (e.g. Network for WAF/compliance)
let verboseLogging = false;
//: Whether the right-hand insights panel itself is expanded — independent of
//: which individual tabs (Cost/WAF/Governance/Suggestions/Logs) are toggled
//: on. Turning a toggle on auto-opens the panel as a convenience; turning
//: one off only hides that tab, it never auto-collapses the panel. Only the
//: X button / collapsed-rail reopen affordances change this directly.
let rightPanelOpen = false;
const RIGHT_PANEL_OPEN_KEY = 'drawio-agent-right-panel-open';
let validationPanelActive = false;
let logEntryCount = 0;
let vlmAutoRetryCount = 0;
let currentSessionId = null;

function ensureSessionId() {
    if (currentSessionId) return currentSessionId;
    currentSessionId = sessionStorage.getItem(SESSION_ID_KEY)
        || (crypto.randomUUID ? crypto.randomUUID() : 's_' + Date.now() + '_' + Math.random().toString(36).slice(2));
    sessionStorage.setItem(SESSION_ID_KEY, currentSessionId);
    return currentSessionId;
}

function startNewSession() {
    currentSessionId = crypto.randomUUID ? crypto.randomUUID() : 's_' + Date.now() + '_' + Math.random().toString(36).slice(2);
    sessionStorage.setItem(SESSION_ID_KEY, currentSessionId);
    return currentSessionId;
}

/** Points the active session at an existing id (opening a past session from
 * the Sessions picker) rather than minting a new one. */
function setActiveSessionId(sessionId) {
    currentSessionId = sessionId;
    sessionStorage.setItem(SESSION_ID_KEY, sessionId);
    return currentSessionId;
}

/** Keeps the URL's ?session= param in sync with the active session, so a
 * past session opened via the Sessions picker can be bookmarked/shared and
 * reloaded straight back into it — pass null to clear it (New chat). */
function updateSessionUrlParam(sessionId) {
    const url = new URL(window.location.href);
    if (sessionId) url.searchParams.set('session', sessionId);
    else url.searchParams.delete('session');
    window.history.replaceState({}, '', url);
}

const EXAMPLE_PROMPTS = {
    aws: [
        'Highly available web app with API Gateway, Fargate in public/private subnets, Aurora, ElastiCache, CloudFront, Route 53, WAF, and CloudWatch with Overview panel',
        'Serverless data pipeline: S3 → Lambda → SQS → ECS workers → Redshift with KMS encryption with Overview panel',
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
    {
        id: 'aws_auth',
        title: 'AWS Auth (Serverless)',
        desc: 'React SPA auth with Cognito, API Gateway, Lambda, and DynamoDB',
        platforms: ['aws'],
        prompt: 'Generate an authentication architecture using a React SPA with AWS Serverless: CloudFront + S3 for the frontend, Cognito User Pool for auth, API Gateway + Lambda for protected APIs, DynamoDB for user data, Secrets Manager, and CloudWatch. Show login, MFA, and session flow edges.',
    },
    {
        id: 'rag_chat',
        title: 'RAG Chat App',
        desc: 'Retrieval-augmented generation with ingestion pipeline and chat API',
        platforms: ['aws', 'gcp', 'azure'],
        prompt: 'Generate a RAG architecture for a chat application: user chat UI, API gateway, orchestration function, vector store, embedding model, LLM endpoint, object storage for documents, ingestion pipeline (upload → parse → embed → index), and monitoring. Use connected flows for data ingestion vs query path.',
    },
];

const GALLERY_ICONS = {
    three_tier: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="8" height="5" rx="1"/><rect x="14" y="2" width="8" height="5" rx="1"/><rect x="8" y="9.5" width="8" height="5" rx="1"/><rect x="2" y="17" width="8" height="5" rx="1"/><rect x="14" y="17" width="8" height="5" rx="1"/><path d="M6 7v2.5M18 7v2.5M12 14.5V17M6 12v5M18 12v5"/></svg>',
    serverless: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/><circle cx="12" cy="12" r="10" stroke-dasharray="3 3"/></svg>',
    event_driven: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="12" r="3"/><circle cx="19" cy="12" r="3"/><circle cx="12" cy="5" r="2.5"/><circle cx="12" cy="19" r="2.5"/><path d="M7.5 10.5L9.5 7M16.5 10.5L14.5 7M7.5 13.5L9.5 17M16.5 13.5L14.5 17"/></svg>',
    data_analytics: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12v6"/><path d="M12 15v3"/><path d="M7 14v4"/></svg>',
    k8s_microservices: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="18" r="2"/><path d="M7 7l3 3M14 7l3 3M7 17l3-3M14 17l3-3"/></svg>',
    static_site: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/><rect x="9" y="7" width="6" height="4" rx="1"/><path d="M9 15h6M10 13v2M14 13v2"/></svg>',
    aws_auth: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/><circle cx="12" cy="16" r="1"/></svg>',
    rag_chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><path d="M8 9h8M8 13h5"/></svg>',
};

const PATTERN_LABELS = {
    three_tier_web: 'HA 3-Tier Web',
    serverless_api: 'Serverless API',
    event_driven: 'Event-Driven',
    data_analytics: 'Data Analytics',
    microservices_k8s: 'Microservices',
    static_site_cdn: 'Static Site + CDN',
};
