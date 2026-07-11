# Common Azure Architecture Patterns

Ready-to-use patterns for Azure architecture diagrams.

## 1. Web Application (3-Tier)

```python
from diagrams import Diagram, Cluster
from diagrams.azure.network import ApplicationGateway, CDNProfiles
from diagrams.azure.compute import AppServices
from diagrams.azure.database import SQLDatabases, CacheForRedis
from diagrams.azure.storage import BlobStorage
from diagrams.azure.security import KeyVaults

with Diagram("Web Application", show=False, direction="TB", outformat="png"):
    cdn = CDNProfiles("CDN")
    
    with Cluster("Frontend"):
        gateway = ApplicationGateway("App Gateway")
        web = AppServices("Web App")
    
    with Cluster("Backend"):
        api = AppServices("API")
        cache = CacheForRedis("Redis")
    
    with Cluster("Data"):
        db = SQLDatabases("SQL Database")
        storage = BlobStorage("Static Assets")
    
    kv = KeyVaults("Key Vault")
    
    cdn >> gateway >> web >> api
    api >> [cache, db]
    api >> kv
    web >> storage
```

## 2. Microservices with AKS

```python
from diagrams import Diagram, Cluster
from diagrams.azure.compute import AKS, ACR
from diagrams.azure.network import ApplicationGateway
from diagrams.azure.database import CosmosDb, CacheForRedis
from diagrams.azure.integration import ServiceBus
from diagrams.azure.security import KeyVaults
from diagrams.azure.monitor import ApplicationInsights

with Diagram("Microservices Architecture", show=False, direction="LR", outformat="png"):
    with Cluster("Ingress"):
        gateway = ApplicationGateway("App Gateway")
    
    with Cluster("AKS Cluster"):
        acr = ACR("Container Registry")
        aks = AKS("AKS")
    
    with Cluster("Data Services"):
        cosmos = CosmosDb("Cosmos DB")
        redis = CacheForRedis("Redis")
    
    with Cluster("Messaging"):
        bus = ServiceBus("Service Bus")
    
    insights = ApplicationInsights("App Insights")
    kv = KeyVaults("Key Vault")
    
    gateway >> aks
    acr >> aks
    aks >> [cosmos, redis, bus]
    aks >> kv
    aks >> insights
```

## 3. Serverless / Event-Driven

```python
from diagrams import Diagram, Cluster
from diagrams.azure.compute import FunctionApps
from diagrams.azure.integration import EventGridTopics, ServiceBus, LogicApps
from diagrams.azure.storage import BlobStorage, QueuesStorage
from diagrams.azure.database import CosmosDb

with Diagram("Serverless Event-Driven", show=False, direction="LR", outformat="png"):
    with Cluster("Event Sources"):
        blob = BlobStorage("Blob Trigger")
        queue = QueuesStorage("Queue Trigger")
        eventgrid = EventGridTopics("Event Grid")
    
    with Cluster("Processing"):
        func1 = FunctionApps("Processor 1")
        func2 = FunctionApps("Processor 2")
        logic = LogicApps("Orchestrator")
    
    with Cluster("Output"):
        bus = ServiceBus("Service Bus")
        cosmos = CosmosDb("Cosmos DB")
    
    blob >> func1 >> cosmos
    queue >> func2 >> bus
    eventgrid >> logic >> [func1, func2]
```

## 4. Data Platform / Analytics

```python
from diagrams import Diagram, Cluster
from diagrams.azure.analytics import DataFactories, Databricks, SynapseAnalytics, EventHubs
from diagrams.azure.storage import DataLakeStorage, BlobStorage
from diagrams.azure.database import SQLDatabases
from diagrams.azure.ml import MachineLearningServiceWorkspaces

with Diagram("Data Platform", show=False, direction="LR", outformat="png"):
    with Cluster("Sources"):
        blob = BlobStorage("Raw Data")
        events = EventHubs("Streaming")
        sql = SQLDatabases("Operational DB")
    
    with Cluster("Ingestion"):
        adf = DataFactories("Data Factory")
    
    with Cluster("Storage"):
        lake = DataLakeStorage("Data Lake")
    
    with Cluster("Processing"):
        databricks = Databricks("Databricks")
        synapse = SynapseAnalytics("Synapse")
    
    ml = MachineLearningServiceWorkspaces("ML Workspace")
    
    [blob, events, sql] >> adf >> lake
    lake >> databricks >> synapse
    databricks >> ml
```

## 5. Hub-Spoke Networking

```python
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.network import VirtualNetworks, Firewall, VirtualNetworkGateways
from diagrams.azure.networking import Bastions
from diagrams.azure.compute import VM
from diagrams.azure.database import SQLDatabases

with Diagram("Hub-Spoke Network", show=False, direction="TB", outformat="png"):
    with Cluster("Hub VNet"):
        firewall = Firewall("Azure Firewall")
        bastion = Bastions("Bastion")
        vpn = VirtualNetworkGateways("VPN Gateway")
    
    with Cluster("Spoke 1 - Web"):
        web_vm = VM("Web Server")
    
    with Cluster("Spoke 2 - Data"):
        db = SQLDatabases("SQL Database")
    
    onprem = VirtualNetworkGateways("On-Premises")
    
    onprem >> Edge(label="VPN") >> vpn >> firewall
    web_vm >> Edge(label="Peering") >> firewall
    db >> Edge(label="Peering") >> firewall
    bastion >> [web_vm, db]
```

## 6. API-First Architecture

```python
from diagrams import Diagram, Cluster
from diagrams.azure.integration import APIManagement, LogicApps, ServiceBus
from diagrams.azure.compute import FunctionApps, AppServices
from diagrams.azure.database import CosmosDb
from diagrams.azure.security import KeyVaults
from diagrams.azure.identity import ActiveDirectory

with Diagram("API-First Architecture", show=False, direction="TB", outformat="png"):
    users = ActiveDirectory("Entra ID")
    
    with Cluster("API Layer"):
        apim = APIManagement("API Management")
    
    with Cluster("Backend Services"):
        app = AppServices("Core API")
        func = FunctionApps("Async Processor")
        logic = LogicApps("Integrations")
    
    with Cluster("Data"):
        cosmos = CosmosDb("Cosmos DB")
        bus = ServiceBus("Service Bus")
    
    kv = KeyVaults("Key Vault")
    
    users >> apim >> [app, func, logic]
    app >> cosmos
    func >> bus
    logic >> bus
    [app, func, logic] >> kv
```

## 7. IoT Solution

```python
from diagrams import Diagram, Cluster
from diagrams.azure.iot import IotHub, IotEdge, DigitalTwins
from diagrams.azure.analytics import StreamAnalyticsJobs
from diagrams.azure.storage import BlobStorage
from diagrams.azure.compute import FunctionApps
from diagrams.azure.database import CosmosDb

with Diagram("IoT Architecture", show=False, direction="LR", outformat="png"):
    with Cluster("Edge"):
        edge = IotEdge("IoT Edge")
    
    with Cluster("Ingestion"):
        hub = IotHub("IoT Hub")
    
    with Cluster("Processing"):
        stream = StreamAnalyticsJobs("Stream Analytics")
        func = FunctionApps("Functions")
    
    with Cluster("Storage"):
        twins = DigitalTwins("Digital Twins")
        cosmos = CosmosDb("Warm Storage")
        blob = BlobStorage("Cold Storage")
    
    edge >> hub >> stream
    stream >> [cosmos, blob]
    hub >> func >> twins
```

## 8. DevOps / CI/CD Pipeline

```python
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.devops import Repos, Pipelines, Artifacts
from diagrams.azure.compute import AKS, ACR
from diagrams.azure.security import KeyVaults
from diagrams.azure.monitor import ApplicationInsights

with Diagram("DevOps Pipeline", show=False, direction="LR", outformat="png"):
    with Cluster("Source Control"):
        repos = Repos("Azure Repos")
    
    with Cluster("Build"):
        build = Pipelines("Build Pipeline")
        artifacts = Artifacts("Artifacts")
    
    with Cluster("Release"):
        release = Pipelines("Release Pipeline")
    
    with Cluster("Environments"):
        acr = ACR("Container Registry")
        aks_dev = AKS("Dev")
        aks_prod = AKS("Prod")
    
    kv = KeyVaults("Key Vault")
    insights = ApplicationInsights("App Insights")
    
    repos >> build >> artifacts >> release
    release >> acr >> [aks_dev, aks_prod]
    release >> kv
    [aks_dev, aks_prod] >> insights
```

## 9. Multi-Region High Availability

```python
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.network import FrontDoors
from diagrams.azure.compute import AppServices
from diagrams.azure.database import CosmosDb, SQLDatabases
from diagrams.azure.storage import BlobStorage

with Diagram("Multi-Region HA", show=False, direction="TB", outformat="png"):
    frontdoor = FrontDoors("Front Door")
    
    with Cluster("Region 1 - Primary"):
        app1 = AppServices("App Service")
        sql1 = SQLDatabases("SQL (Primary)")
    
    with Cluster("Region 2 - Secondary"):
        app2 = AppServices("App Service")
        sql2 = SQLDatabases("SQL (Secondary)")
    
    cosmos = CosmosDb("Cosmos DB\n(Multi-Region)")
    blob = BlobStorage("Blob\n(GRS)")
    
    frontdoor >> [app1, app2]
    app1 >> [sql1, cosmos]
    app2 >> [sql2, cosmos]
    sql1 >> Edge(label="Geo-Replication", style="dashed") >> sql2
    [app1, app2] >> blob
```

## 10. Zero Trust Security

```python
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.identity import ActiveDirectory, ConditionalAccess, ManagedIdentities
from diagrams.azure.network import Firewall, ApplicationGateway
from diagrams.azure.security import KeyVaults, Sentinel, Defender
from diagrams.azure.compute import AppServices
from diagrams.azure.database import SQLDatabases

with Diagram("Zero Trust Architecture", show=False, direction="TB", outformat="png"):
    with Cluster("Identity"):
        aad = ActiveDirectory("Entra ID")
        ca = ConditionalAccess("Conditional Access")
    
    with Cluster("Network Security"):
        waf = ApplicationGateway("WAF")
        firewall = Firewall("Firewall")
    
    with Cluster("Application"):
        app = AppServices("App Service")
        mi = ManagedIdentities("Managed Identity")
    
    with Cluster("Data"):
        sql = SQLDatabases("SQL")
        kv = KeyVaults("Key Vault")
    
    with Cluster("Security Operations"):
        sentinel = Sentinel("Sentinel")
        defender = Defender("Defender")
    
    aad >> ca >> waf >> app
    app >> mi >> [kv, sql]
    firewall >> [app, sql]
    [app, sql] >> sentinel
    defender >> [app, sql]
```

## 11. AI/ML Solution

```python
from diagrams import Diagram, Cluster
from diagrams.azure.ml import MachineLearningServiceWorkspaces, CognitiveServices
from diagrams.azure.compute import FunctionApps, AKS
from diagrams.azure.storage import BlobStorage
from diagrams.azure.database import CosmosDb
from diagrams.azure.integration import APIManagement

with Diagram("AI/ML Architecture", show=False, direction="LR", outformat="png"):
    with Cluster("Data"):
        blob = BlobStorage("Training Data")
        cosmos = CosmosDb("Feature Store")
    
    with Cluster("ML Platform"):
        mlws = MachineLearningServiceWorkspaces("ML Workspace")
        cognitive = CognitiveServices("Cognitive Services")
    
    with Cluster("Serving"):
        aks = AKS("Model Serving")
        func = FunctionApps("Inference API")
    
    apim = APIManagement("API Management")
    
    blob >> mlws >> aks
    cosmos >> mlws
    cognitive >> func
    [aks, func] >> apim
```

## 12. Hybrid Cloud

```python
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.network import VirtualNetworkGateways, ExpressrouteCircuits, VirtualNetworks
from diagrams.azure.compute import AppServices, VM
from diagrams.azure.database import SQLDatabases
from diagrams.azure.integration import ServiceBus
from diagrams.onprem.database import MSSQL
from diagrams.onprem.compute import Server

with Diagram("Hybrid Cloud", show=False, direction="LR", outformat="png"):
    with Cluster("On-Premises"):
        onprem_server = Server("App Server")
        onprem_db = MSSQL("SQL Server")
    
    with Cluster("Connectivity"):
        expressroute = ExpressrouteCircuits("ExpressRoute")
        vpn = VirtualNetworkGateways("VPN Gateway")
    
    with Cluster("Azure"):
        with Cluster("VNet"):
            vnet = VirtualNetworks("Hub VNet")
            app = AppServices("App Service")
            sql = SQLDatabases("Azure SQL")
        bus = ServiceBus("Service Bus")
    
    onprem_server >> expressroute >> vnet
    onprem_db >> Edge(label="Data Sync", style="dashed") >> sql
    app >> bus >> onprem_server
```

---

## Modern patterns (diagrams 0.25.x icons)

These exploit the icon modules added in diagrams 0.25.x (`aimachinelearning`,
`containers`/Container Apps, `managementgovernance`). All three are verified to render
against the pinned library.

## 13. Azure AI / RAG Application

Retrieval-augmented generation using the modern `aimachinelearning` icons (AI Foundry,
Azure OpenAI, AI Search):

```python
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.aimachinelearning import AzureOpenai, CognitiveSearch, AIStudio
from diagrams.azure.compute import AppServices
from diagrams.azure.database import CosmosDb
from diagrams.azure.storage import BlobStorage
from diagrams.azure.security import KeyVaults
from diagrams.azure.integration import APIManagement
from diagrams.onprem.client import Users

with Diagram("Azure AI - RAG Application", show=False, direction="LR", outformat="png"):
    users = Users("Users")
    with Cluster("Application"):
        apim = APIManagement("API Management")
        app = AppServices("Chat App")
    with Cluster("AI Services"):
        foundry = AIStudio("AI Foundry")
        gpt = AzureOpenai("Azure OpenAI\n(GPT-4o)")
        search = CognitiveSearch("AI Search\n(vector index)")
    with Cluster("Knowledge & State"):
        docs = BlobStorage("Documents")
        cosmos = CosmosDb("Chat history")
    kv = KeyVaults("Key Vault")

    users >> apim >> app
    app >> foundry >> gpt
    app >> search >> docs
    app >> cosmos
    app >> Edge(style="dashed") >> kv
```

## 14. Container Apps Microservices

Serverless containers on a Container Apps environment, pulling images from ACR and
authenticating with a managed identity:

```python
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.compute import ContainerApps, ACR
from diagrams.azure.integration import ServiceBus, APIManagement
from diagrams.azure.database import CosmosDb
from diagrams.azure.identity import ManagedIdentities

with Diagram("Container Apps Microservices", show=False, direction="LR", outformat="png"):
    apim = APIManagement("API Management")
    acr = ACR("Container Registry")
    with Cluster("Container Apps Environment"):
        api = ContainerApps("API service")
        worker = ContainerApps("Worker")
        web = ContainerApps("Web frontend")
    bus = ServiceBus("Service Bus")
    cosmos = CosmosDb("Cosmos DB")
    mi = ManagedIdentities("Managed Identity")

    apim >> web >> api
    api >> bus >> worker
    api >> cosmos
    acr >> Edge(style="dotted", label="image pull") >> api
    api >> Edge(style="dashed") >> mi
```

## 15. Landing Zone Governance

Management-group hierarchy with Policy and Blueprints, Defender/Sentinel and Azure Arc,
using the `managementgovernance` module:

```python
from diagrams import Diagram, Cluster
from diagrams.azure.managementgovernance import Policy, Blueprints, AzureArc, Monitor
from diagrams.azure.general import ManagementGroups, Subscriptions
from diagrams.azure.security import Defender, Sentinel

with Diagram("Landing Zone Governance", show=False, direction="TB", outformat="png"):
    with Cluster("Management Group hierarchy"):
        mg = ManagementGroups("Platform MG")
        sub_conn = Subscriptions("Connectivity")
        sub_mgmt = Subscriptions("Management")
        sub_lz = Subscriptions("Landing Zones")
    with Cluster("Governance"):
        policy = Policy("Azure Policy")
        bp = Blueprints("Blueprints")
    with Cluster("Security & Ops"):
        defender = Defender("Defender for Cloud")
        sentinel = Sentinel("Sentinel")
        monitor = Monitor("Azure Monitor")
    arc = AzureArc("Azure Arc")

    mg >> [sub_conn, sub_mgmt, sub_lz]
    policy >> mg
    bp >> mg
    sub_lz >> defender >> sentinel
    sub_mgmt >> monitor
    arc >> sub_mgmt
```

## 16. Accessible diagrams

Make diagrams readable for colourblind viewers (~8% of men): never encode meaning by
colour alone - pair colour with a label, line style, or shape. Use a colourblind-safe
palette such as Wong (2011): `#000000` black, `#E69F00` orange, `#56B4E9` sky blue,
`#009E73` green, `#F0E442` yellow, `#0072B2` blue, `#D55E00` vermillion, `#CC79A7` purple.

```python
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.compute import AppServices
from diagrams.azure.database import SQLDatabases

# Wong colourblind-safe palette
PRIMARY, REPLICA = "#0072B2", "#D55E00"

with Diagram("Accessible Colour Use", show=False, outformat="png"):
    with Cluster("Primary region", graph_attr={"bgcolor": "#E6F2F8"}):
        app = AppServices("App (primary)")
        db = SQLDatabases("SQL (primary)")
    replica = SQLDatabases("SQL (replica)")
    app >> Edge(color=PRIMARY, label="writes") >> db
    db >> Edge(color=REPLICA, style="dashed", label="geo-replication") >> replica
```

Pairing colour with a dashed style and a label means the meaning survives in greyscale.

## 17. Hub-spoke with CIDR annotations

```python
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.network import VirtualNetworks, Firewall, VirtualNetworkGateways, PrivateEndpoint
from diagrams.azure.networking import Bastions
from diagrams.azure.compute import VM

with Diagram("Hub-Spoke with CIDRs", show=False, direction="TB", outformat="png"):
    with Cluster("Hub VNet (10.0.0.0/16)"):
        fw = Firewall("Azure Firewall\n10.0.1.0/24")
        bastion = Bastions("Bastion\n10.0.2.0/24")
        gw = VirtualNetworkGateways("VPN Gateway\n10.0.255.0/27")
        hub = VirtualNetworks("Hub")
    with Cluster("Spoke 1 - Workload (10.1.0.0/16)"):
        s1 = VirtualNetworks("Spoke 1")
        vm1 = VM("App\n10.1.1.0/24")
    with Cluster("Spoke 2 - Data (10.2.0.0/16)"):
        s2 = VirtualNetworks("Spoke 2")
        pe = PrivateEndpoint("PE\n10.2.1.0/24")
    fw >> hub
    hub >> Edge(label="peering") >> s1
    hub >> Edge(label="peering") >> s2
```

## 18. Private endpoint DNS resolution

```python
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.network import PrivateEndpoint, DNSPrivateZones
from diagrams.azure.database import SQLDatabases
from diagrams.azure.compute import VM

with Diagram("Private Endpoint DNS Resolution", show=False, direction="LR", outformat="png"):
    with Cluster("Spoke VNet"):
        vm = VM("App VM")
        pe = PrivateEndpoint("Private Endpoint")
    dns = DNSPrivateZones("privatelink.database.windows.net")
    sql = SQLDatabases("Azure SQL\n(public access disabled)")
    vm >> Edge(label="1. resolve FQDN") >> dns
    vm >> Edge(label="2. private IP") >> pe
    pe >> Edge(label="3. Private Link") >> sql
```

---

## Tips for Professional Diagrams

### Consistent Direction
- `direction="TB"` (top-bottom) for hierarchical architectures
- `direction="LR"` (left-right) for flow/pipeline diagrams

### Meaningful Clustering
- Group by: Resource Group, Subnet, Service tier, or Logical function
- Avoid too many nested clusters (max 2-3 levels)

### Edge Styling
- Solid lines: Primary data flow
- Dashed lines: Configuration, secrets, replication
- Colored lines: Highlight critical paths

### Labels
- Keep node labels short (1-3 words)
- Add context in cluster names
- Use edge labels sparingly
