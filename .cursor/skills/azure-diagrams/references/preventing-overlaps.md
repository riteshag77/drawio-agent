# Preventing Overlaps in Complex Diagrams

Guide for avoiding node and edge overlaps in Graphviz-based diagrams.

## Common Rendering Issues

### Issue 1: Floating/Orphaned Labels

**Symptom**: Labels appear disconnected from edges, floating in empty space.

**Cause**: Using `label` on edges that Graphviz struggles to position.

**Fix**: Use `xlabel` (external label) or `headlabel`/`taillabel`:

```python
# Instead of:
dot.edge('a', 'b', label='1. Redirect')

# Use xlabel for better positioning:
dot.edge('a', 'b', xlabel='1. Redirect')

# Or use head/tail labels for specific placement:
dot.edge('a', 'b', headlabel='Token', taillabel='Request')
```

**For sequence-style numbered flows**:
```python
# Don't label edges - use intermediate nodes instead
dot.node('step1', '1. Redirect', shape='plaintext')
dot.edge('user', 'step1', arrowhead='none')
dot.edge('step1', 'app')
```

### Issue 2: Excessive Whitespace

**Symptom**: Large empty areas at top/bottom/sides of diagram.

**Cause**: Default graph sizing or margin settings.

**Fix**: Control the bounding box:

```python
dot.attr(
    pad='0.2',           # Reduce padding (default can be large)
    margin='0',          # Reduce margins
    ratio='compress',    # Compress to fit content
    # Or set explicit size:
    size='10,10!',       # Width,Height in inches (! = force exact)
)
```

**For the diagrams library** (Python diagrams):
```python
with Diagram(
    "Title",
    graph_attr={
        "pad": "0.2",
        "margin": "0",
        "ratio": "compress",
    }
):
```

### Issue 3: Labels Repeating/Duplicating

**Symptom**: Same label text appears multiple times.

**Cause**: Labels defined on both graph and individual elements, or loop creating duplicate nodes.

**Fix**: Ensure unique node IDs and don't duplicate labels:

```python
# Wrong - creates duplicate labels
for i, env in enumerate(['Dev', 'Test', 'Prod']):
    dot.node(f'deploy_{i}', 'Deployment Pipeline')  # Same label 3x!

# Correct - unique labels or single shared node
dot.node('deploy_dev', 'Deploy to Dev')
dot.node('deploy_test', 'Deploy to Test')
dot.node('deploy_prod', 'Deploy to Prod')

# Or use single node with edges from multiple sources
dot.node('pipeline', 'Deployment Pipeline')
dot.edge('dev', 'pipeline')
dot.edge('test', 'pipeline')
dot.edge('prod', 'pipeline')
```

## Key Graph Attributes

```python
from graphviz import Digraph

dot = Digraph('Diagram')
dot.attr(
    # Increase spacing between nodes
    nodesep='1.0',      # Horizontal spacing (default 0.25)
    ranksep='1.0',      # Vertical spacing between ranks (default 0.5)
    
    # Add padding around the graph
    pad='0.5',
    margin='0.5',
    
    # Overlap prevention
    overlap='false',     # Prevent node overlaps (for neato/fdp engines)
    splines='spline',    # Curved lines that route around nodes
    
    # For very complex diagrams
    sep='+25,25',        # Minimum separation added to nodes
)
```

## Spline Types - Choosing the Right Edge Style

| Spline Type | Best For | Pros | Cons |
|-------------|----------|------|------|
| `spline` | General architecture diagrams | Smooth curves, avoids nodes | Can look busy with many edges |
| `ortho` | Pipeline/flow diagrams | Clean right-angles, professional | Labels may not display, can fail with complex graphs |
| `polyline` | Fallback when ortho fails | Reliable, follows angles | Less elegant than ortho |
| `line` | Simple diagrams | Direct, fast rendering | Lines may cross nodes |

**Recommendation by diagram type:**

```python
# Pipeline / CI-CD diagrams (left-to-right flow)
graph_attr = {"splines": "ortho", "rankdir": "LR"}

# Architecture diagrams (hierarchical, top-down)
graph_attr = {"splines": "spline", "rankdir": "TB"}

# Data flow diagrams
graph_attr = {"splines": "spline", "rankdir": "LR"}

# Network topology (complex connections)
graph_attr = {"splines": "spline", "overlap": "false"}
```

**Note**: With `splines="ortho"`, edge labels may not render. Use `xlabel` instead of `label`:
```python
# With ortho splines, use xlabel
dot.edge('a', 'b', xlabel='connection')  # Works
dot.edge('a', 'b', label='connection')   # May not display
```

## Recommended Settings by Diagram Complexity

### Simple (< 10 nodes)
```python
dot.attr(nodesep='0.5', ranksep='0.75')
```

## Label Best Practices

### Keep Labels Short

Labels should fit within their nodes. Use line breaks for longer text:

```python
# Too long - will overflow
node = AKS("Azure Kubernetes Service Production Cluster")

# Better - use abbreviations and line breaks
node = AKS("AKS Cluster\nProduction")

# Or just the essentials
node = AKS("AKS")
```

### Line Break Syntax

```python
# In diagrams library (Python)
node = CosmosDb("Cosmos DB\nProducts")

# In graphviz
dot.node('cosmos', 'Cosmos DB\nProducts')
```

### Semantic Labels

Use labels that communicate function, not just technology:

```python
# Less informative
sql = SQLDatabases("SQL")

# More informative  
sql = SQLDatabases("Orders DB")

# With tier/environment info
sql = SQLDatabases("Orders\n(S3 tier)")
```

### Medium (10-25 nodes)
```python
dot.attr(nodesep='0.8', ranksep='1.0', pad='0.5')
```

### Complex (25+ nodes) - Like the DMS Architecture
```python
dot.attr(
    nodesep='1.2',       # More horizontal space
    ranksep='1.2',       # More vertical space  
    pad='0.75',
    splines='spline',    # Curved edges route better
    concentrate='false', # Don't merge edges (can cause confusion)
)
```

## Fixing Specific Overlap Issues

### Problem: Database cylinder overlapping adjacent nodes

**Solution 1: Increase node width**
```python
dot.node('database', 'DMS Database\nSQL Server 2008 R2', 
         shape='cylinder', width='2.0', height='1.5')
```

**Solution 2: Use rank constraints to force positioning**
```python
# Force nodes to be on the same horizontal level
with dot.subgraph() as s:
    s.attr(rank='same')
    s.node('node1')
    s.node('node2')
    s.node('node3')

# Put database on its own rank below
with dot.subgraph() as s:
    s.attr(rank='same')
    s.node('database')
```

**Solution 3: Add invisible spacer nodes**
```python
dot.node('spacer1', '', style='invis', width='0.5')
dot.edge('node_before', 'spacer1', style='invis')
dot.edge('spacer1', 'database', style='invis')
```

### Problem: Edges crossing through nodes

**Solution: Use xlabel instead of label for edge labels**
```python
# Instead of:
dot.edge('a', 'b', label='connection')

# Use xlabel (external label, placed outside the edge):
dot.edge('a', 'b', xlabel='connection')
```

**Solution: Change spline type**
```python
# Try different spline options
dot.attr(splines='spline')    # Curved - usually best
dot.attr(splines='polyline')  # Straight with bends
dot.attr(splines='curved')    # Similar to spline
```

### Problem: Clusters overlapping

**Solution: Add margin inside clusters**
```python
with dot.subgraph(name='cluster_0') as c:
    c.attr(
        margin='20',          # Internal padding
        style='filled',
        fillcolor='#E8F5E9',
    )
```

## Complete Example: DMS-Style Architecture (Fixed)

```python
from graphviz import Digraph

dot = Digraph('DMS Architecture', format='png')
dot.attr(
    rankdir='TB',
    bgcolor='white',
    fontname='Segoe UI',
    nodesep='1.0',        # KEY: More horizontal space
    ranksep='1.0',        # KEY: More vertical space
    pad='0.5',
    splines='spline',
)
dot.attr('node', fontname='Segoe UI', fontsize='10')
dot.attr('edge', fontname='Segoe UI', fontsize='9')

# External Interfaces
with dot.subgraph(name='cluster_external') as c:
    c.attr(label='EXTERNAL INTERFACES', style='filled', fillcolor='#F3E5F5', 
           color='#9C27B0', fontcolor='#9C27B0', margin='20')
    c.node('scanners', 'Scanners\n(Kyocera)', shape='box', style='filled', fillcolor='white')
    c.node('email', 'Email\n(SMTP)', shape='box', style='filled', fillcolor='white')
    c.node('citizens', 'Citizens/Staff\n(Manual Upload)', shape='box', style='filled', fillcolor='white')
    c.node('webforms', 'Web Forms\nPortal', shape='box', style='filled', fillcolor='white')

# DMS Document Management
with dot.subgraph(name='cluster_dms') as c:
    c.attr(label='DMS DOCUMENT MANAGEMENT', style='filled', fillcolor='#E8F5E9',
           color='#4CAF50', fontcolor='#4CAF50', margin='20')
    
    # Application Server row
    with c.subgraph(name='cluster_appserver') as app:
        app.attr(label='DMS Application Server', style='filled', fillcolor='#C8E6C9', margin='15')
        app.node('workflow', 'Workflow\nEngine', shape='box', style='filled', fillcolor='white')
        app.node('template', 'Template Engine\n(RTF)', shape='box', style='filled', fillcolor='white')
        app.node('indexing', 'Document\nIndexing', shape='box', style='filled', fillcolor='white')
        app.node('ui', 'User\nInterface', shape='box', style='filled', fillcolor='white')
        app.node('reporting', 'Reporting\nModule', shape='box', style='filled', fillcolor='white')
        app.node('security', 'Security\n(Windows Auth)', shape='box', style='filled', fillcolor='white')
    
    # Database on its own rank with more space
    c.node('docdb', 'DMS Database\nSQL Server 2008 R2', 
           shape='cylinder', style='filled', fillcolor='#2196F3', fontcolor='white',
           width='2.5', height='1.2')  # Explicit size

# SSIS Integration Layer
with dot.subgraph(name='cluster_ssis') as c:
    c.attr(label='SSIS INTEGRATION LAYER', style='filled', fillcolor='#FFF3E0',
           color='#FF9800', fontcolor='#FF9800', margin='20')
    c.node('csv_import', 'CSV Import\nPackage', shape='box', style='filled', fillcolor='#FFB74D')
    c.node('csv_export', 'CSV Export\nPackage', shape='box', style='filled', fillcolor='#FFB74D')
    c.node('erp_sync', 'ERP Sync\nPackage', shape='box', style='filled', fillcolor='#FFB74D')
    c.node('webforms_pkg', 'Web Forms\nPackage', shape='box', style='filled', fillcolor='#FFB74D')
    c.node('email_proc', 'Email\nProcessing', shape='box', style='filled', fillcolor='#FFB74D')
    c.node('archive', 'Archive\nCleanup', shape='box', style='filled', fillcolor='#FFB74D')

# Backend Systems
with dot.subgraph(name='cluster_backend') as c:
    c.attr(label='BACKEND SYSTEMS', style='filled', fillcolor='#E8EAF6',
           color='#3F51B5', fontcolor='#3F51B5', margin='20')
    
    with c.subgraph(name='cluster_erp') as erp:
        erp.attr(label='Finance ERP', style='filled', fillcolor='#C5CAE9', margin='15')
        erp.node('claims_mod', 'Claims\n(Tier 1)', shape='box', style='filled', fillcolor='white')
        erp.node('billing', 'Billing\nModule', shape='box', style='filled', fillcolor='white')
        erp.node('invoicing', 'Invoicing\n(Tier 2)', shape='box', style='filled', fillcolor='white')
        erp.node('payments_mod', 'Payments\nModule', shape='box', style='filled', fillcolor='white')
        erp.node('receivables_mod', 'Receivables\nModule', shape='box', style='filled', fillcolor='white')
        erp.node('collections_mod', 'Collections\nModule', shape='box', style='filled', fillcolor='white')

# Connections
dot.edge('scanners', 'indexing')
dot.edge('email', 'indexing')
dot.edge('citizens', 'indexing')
dot.edge('webforms', 'ui', style='dashed')

dot.edge('workflow', 'docdb')
dot.edge('template', 'docdb')
dot.edge('indexing', 'docdb')
dot.edge('ui', 'docdb')
dot.edge('reporting', 'docdb')

dot.edge('docdb', 'csv_import', xlabel='SSIS Packages\n(Scheduled)')
dot.edge('docdb', 'csv_export')
dot.edge('docdb', 'erp_sync')

dot.edge('erp_sync', 'claims_mod')
dot.edge('erp_sync', 'billing')
dot.edge('erp_sync', 'invoicing')

dot.render('dms-architecture-fixed', cleanup=True)
print("Generated: dms-architecture-fixed.png")
```

## Troubleshooting Checklist

1. **Increase `nodesep`** - horizontal spacing between nodes
2. **Increase `ranksep`** - vertical spacing between ranks
3. **Use `rank='same'`** subgraphs to force horizontal alignment
4. **Set explicit `width` and `height`** on large nodes (cylinders, etc.)
5. **Add `margin`** inside clusters
6. **Use `splines='spline'`** for curved edges that route around nodes
7. **Try `xlabel`** instead of `label` for edge labels
8. **Add invisible spacer nodes** if needed

## Quick Fix Template

If a diagram has overlaps, add these attributes:

```python
dot.attr(
    nodesep='1.2',
    ranksep='1.2', 
    pad='0.5',
    splines='spline',
)
```
