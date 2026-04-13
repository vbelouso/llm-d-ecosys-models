# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Multi-LLM deployment manifests for OpenShift using llm-d framework with Gateway API routing. Each model runs in isolated vLLM decode pods, routed through agentgateway based on the `model` field in request bodies.

## Architecture Layers

The deployment has four distinct layers that must be deployed in order:

1. **Storage Layer**: PVCs → Download pods → Model files
2. **Gateway Layer**: agentgateway Gateway → AgentgatewayPolicy (body-based routing)
3. **Routing Layer**: InferencePools → HTTPRoutes (connect Gateway to model servers via label selectors)
4. **Inference Layer**: llm-d-modelservice (vLLM decode pods)

**Critical**: HTTPRoutes created by InferencePools must be patched to reference the Gateway namespace (`agentgateway-system`) or routing fails with 404.

## Environment Variables

Always use these for consistency:

```bash
export NAMESPACE=llm-d
export MODEL_SERVER=vllm
export IGW_CHART_VERSION=v1.3.0
```

## Adding New Models

**CRITICAL - READ FIRST**: Before creating manifests for ANY new model:

1. **Check vLLM model support**: <https://docs.vllm.ai/projects/recipes/en/latest/index.html>
   - Find the model family recipe (e.g., Google/Gemma4.md, Qwen/Qwen.md)
   - Verify vLLM version requirements
   - Note any special configuration (quantization, context length, etc.)

2. **Check tool calling support**: <https://docs.vllm.ai/en/latest/features/tool_calling/>
   - Identify the correct `--tool-call-parser` for the model family
   - Check if the parser exists in your target vLLM version
   - Verify any additional tool calling arguments needed

**DO NOT GUESS** tool parsers or vLLM versions. Wrong configuration causes silent failures or cryptic KeyErrors.

### 1. Create Manifests Directory

```bash
mkdir -p manifests/model-name/{pvc.yaml,download.yaml,values.yaml}
```

### 2. Key Configuration Decisions

**vLLM Version & Tool Parser** (see documentation links above):

- Each model family may require a different vLLM version and Docker image
- Tool parsers are version-specific - check vLLM recipes for model-specific tags
- Gemma 4 NVFP4 models require nightly: vllm/vllm-openai:nightly (PR #39045)
- Qwen models work on v0.17.0
- Granite 4 models: Use v0.19.0 with `granite4` parser
- GGUF quantizations: Require v0.19.0+ for reliable loading
- **Use nightly for bleeding-edge quantizations** (FP4, NVFP4) merged but not released

**PVC Size**:

- MoE models: ~2-3x parameter count (e.g., 35B → 80Gi)
- Dense models: ~2x parameter count (e.g., 27B → 60Gi)
- Quantized (FP8/FP4): Adjust downward

**Tool Call Parser** (in `values.yaml`):

- Qwen models: `--tool-call-parser hermes` (image: vllm/vllm-openai:v0.17.0)
- Gemma 4 models: `--tool-call-parser gemma4` (image: vllm/vllm-openai:gemma4)
- Granite 4 models: `--tool-call-parser granite4` (image: vllm/vllm-openai:v0.19.0)
- Check vLLM recipes for model-specific Docker images - each family may have dedicated tags

**Common vLLM Args**:

- `--enable-auto-tool-choice` - Enable tool calling
- `--trust-remote-code` - Required for custom model code
- `--disable-access-log-for-endpoints /health,/metrics,/ping` - Reduce noise
- `HOME=/tmp` env var - OpenShift non-root compatibility

**Label Matching**:
The `app` label in `values.yaml` must match the InferencePool's `matchLabels.app`:

```yaml
labels:
  app: "vllm-model-name"  # Used by InferencePool selector
```

### 3. Deployment Sequence

```bash
# Storage
oc apply -f manifests/model-name/pvc.yaml
oc apply -f manifests/model-name/download.yaml
oc wait --for=condition=Ready pod/download-model-name -n llm-d --timeout=7200s
oc delete pod/download-model-name  # MUST DELETE to release RWO PVC

# InferencePool
helm upgrade --install vllm-model-name \
  --set inferencePool.modelServers.matchLabels.app=vllm-model-name \
  --set experimentalHttpRoute.baseModel="HuggingFace/model-id" \
  --set provider.name=none \
  --set inferencePool.modelServerType=vllm \
  --set experimentalHttpRoute.enabled=true \
  --version ${IGW_CHART_VERSION} \
  --namespace llm-d \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool

# Model Server
helm upgrade --install ms-model-name llm-d-modelservice/llm-d-modelservice \
  -f manifests/model-name/values.yaml -n llm-d

# Patch HTTPRoute
oc patch httproute vllm-model-name -n llm-d --type='json' \
  -p='[{"op":"add","path":"/spec/parentRefs/0/namespace","value":"agentgateway-system"}]'
```

## Debugging Workflows

### Model Not Routing (404 errors)

```bash
# Check HTTPRoute has Gateway namespace reference
oc get httproute vllm-model-name -n llm-d -o jsonpath='{.spec.parentRefs[0].namespace}'
# Should output: agentgateway-system

# Check InferencePool matches pods
oc get inferencepool vllm-model-name -n llm-d -o yaml | grep -A5 matchLabels
oc get pods -n llm-d --show-labels | grep vllm-model-name

# Test direct pod access (bypass gateway)
POD=$(oc get pods -n llm-d -l app=vllm-model-name -o name | head -1)
oc exec ${POD} -c vllm -- curl -s http://localhost:8200/v1/models
```

### Model Server Not Starting

```bash
# Check for PVC lock (RWO volumes can only mount to one pod)
oc get pods -n llm-d | grep download-
# If download pods exist, delete them first

# Check vLLM logs for errors
oc logs -f deployment/ms-model-name-llm-d-modelservice-decode -c vllm -n llm-d

# Common errors:
# - "Permission denied /.triton" → Missing HOME=/tmp env var
# - "PVC already in use" → Download pod still running
# - "max context length 1024" → prefill.create should be false
```

### Gateway Not Routing Correctly

```bash
# Verify AgentgatewayPolicy is applied
oc get agentgatewaypolicy -n llm-d

# Check Gateway status
oc get gateway llm-d-gateway -n agentgateway-system -o yaml

# Test routing with explicit model field
curl -X POST https://${GATEWAY_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "Full/HuggingFace/ID", "messages": [{"role": "user", "content": "test"}]}'
```

## Operational Tasks

### Scale to Zero (Free GPU Resources)

See `docs/hacks/scale-to-zero.md` for detailed commands.

Quick reference:

```bash
# Stop all models
oc scale deployment -l helm.sh/chart=llm-d-modelservice-v0.4.9 --replicas=0 -n llm-d

# Start all models
oc scale deployment -l helm.sh/chart=llm-d-modelservice-v0.4.9 --replicas=1 -n llm-d
```

**What's preserved**: PVCs, InferencePools, HTTPRoutes, Services
**What's released**: GPU allocations, memory, running processes

### Query Model List

Gateway doesn't support `GET /v1/models` (AgentgatewayPolicy requires request body). Query pods directly:

```bash
for app in vllm-qwen35-9b vllm-gemma-4-26b-a4b; do
  echo "=== ${app} ==="
  oc get pods -n llm-d -l app=${app} -o name | head -1 | \
    xargs -I{} oc exec {} -c vllm -- curl -s http://localhost:8200/v1/models | jq -r '.data[].id'
done
```

## Architecture Constraints

### Why Prefill Disabled?

`prefill.create: false` in all `values.yaml` files because:

- Prefill/decode disaggregation requires 8+ GPUs with RDMA
- Single-GPU deployments don't benefit from P/D splitting
- Avoids llm-d-inference-sim (simulator) being used instead of vLLM

### Why Download Pods Must Be Deleted?

PVCs use `ReadWriteOnce` (RWO) access mode:

- Only one pod can mount at a time
- Download pods must complete and be deleted before model servers can mount
- PVC data persists after pod deletion

### Why Patch HTTPRoutes?

InferencePools create HTTPRoutes in the model namespace (`llm-d`), but the Gateway lives in `agentgateway-system`. Cross-namespace routing requires explicit namespace reference in `parentRefs`.

### Why Body-Based Routing?

OpenAI API includes model name in request body, not URL path:

```json
{"model": "Qwen/Qwen3.5-9B", "messages": [...]}
```

AgentgatewayPolicy uses CEL expressions to route based on `model` field. This is why `GET /v1/models` returns 404 (no body to parse).

## Version Compatibility

- **vLLM Docker Images**:
  - vllm/vllm-openai:v0.17.0 for Qwen models
  - vllm/vllm-openai:nightly for Gemma 4 NVFP4 models (includes PR #39045)
  - vllm/vllm-openai:gemma4 for official Google Gemma 4 (non-quantized)
  - **Nightly builds**: Use when model card says "requires PR #XXXXX" and PR is merged
  - **Important**: Check vLLM recipes for model-specific tags - many models have dedicated images
- **llm-d-modelservice**: v0.4.8+
- **Gateway API Inference Extension**: v1.3.0
- **OpenShift**: 4.14+ with GPU Operator

## Documentation Structure

- `README.md` - Full deployment guide
- `docs/hacks/scale-to-zero.md` - Operations guide for scaling models
- `.git/info/exclude` - Local-only files (docs/hacks/ is excluded from git)
