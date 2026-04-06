# Models-as-a-Service for multiple LLMs on OpenShift

Production-ready deployment manifests for running multiple large language models on OpenShift with llm-d inference framework and Gateway API routing.

> **Based on:** [Run Model-as-a-Service for Multiple LLMs on OpenShift](https://developers.redhat.com/articles/2026/03/24/run-model-service-multiple-llms-openshift) - Red Hat Developer Blog

## Overview

This repository contains manifests to deploy large language models and embedding models using:

- **llm-d** (Kubernetes-native distributed LLM inference framework)
- **vLLM** serving backend
- **Gateway API Inference Extension** for model routing
- **agentgateway** for body-based routing

### Deployed Models

| Model | Size | Type | Context Length | Use Case |
| ------- | ------ | ------ | ---------------- | ---------- |
| `Qwen/Qwen3.5-35B-A3B` | 35B | MoE (3B active) | 32K | General purpose, efficient |
| `Jackrong/Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled` | 27B | Dense | 32K | Reasoning, Claude-like |
| `Qwen/Qwen3-Next-80B-A3B-Thinking` | 80B | MoE (3B active), FP8 | 32K | Large-scale reasoning |
| `Qwen/Qwen3.5-9B` | 9B | Dense | 32K | Fast inference |
| `sentence-transformers/all-mpnet-base-v2` | 438MB | Embeddings | - | Text embeddings (CPU) |

## Repository Structure

```text
manifests/
├── qwen35-35b-a3b/          # Qwen3.5-35B-A3B model
│   ├── pvc.yaml
│   ├── download.yaml
│   └── values.yaml
├── qwen35-27b-distilled/    # Qwen3.5-27B-distilled model
│   ├── pvc.yaml
│   ├── download.yaml
│   └── values.yaml
├── qwen3-next-80b/          # Qwen3-Next-80B FP8 model
│   ├── pvc.yaml
│   ├── download.yaml
│   └── values.yaml
├── qwen35-9b/               # Qwen3.5-9B model
│   ├── pvc.yaml
│   ├── download.yaml
│   └── values.yaml
├── all-mpnet-base-v2/       # Embeddings model (TEI)
│   ├── pvc.yaml
│   ├── deployment.yaml
│   └── route.yaml
└── shared/                  # Gateway and routing infrastructure
    ├── gateway.yaml
    ├── scc-binding.yaml
    ├── route.yaml
    └── agentgateway-policy.yaml
```

## Architecture

![Architecture Diagram](assets/architecture.png)

The deployment uses Gateway API with body-based routing to direct requests to different model servers:

- **OpenShift Route** → **agentgateway** → **Gateway API HTTPRoutes**
- Body-based routing by `model` field in JSON request
- Each model runs in its own InferencePool with dedicated vLLM decode pod
- Separate TEI deployment for embeddings (CPU, no GPU required)

### Key Design Decisions

- **No Prefill/Decode Disaggregation**: Single-GPU deployments don't benefit from P/D splitting (requires 8+ GPUs with RDMA)
- **Decode-only mode**: Prefill pods disabled (`prefill.create: false`) - all inference runs through decode pods
- **PVC-based model storage**: Models pre-downloaded to RWO PersistentVolumeClaims for fast startup
- **FP8 quantization for 80B**: Qwen3-Next-80B uses FP8-dynamic for memory efficiency
- **vLLM v0.17.0+**: Required for Qwen3.5 support and tool calling features

## Prerequisites

### Required Components

1. **OpenShift 4.14+** with NVIDIA GPU Operator
2. **NVIDIA GPUs** (e.g., H100, H200, A100)
3. **Gateway API CRDs** v1.2.0+
4. **Gateway API Inference Extension** v1.3.0
5. **agentgateway** (llm-d's custom Gateway API implementation)
6. **llm-d-modelservice Helm chart** v0.4.8+
7. **HuggingFace account** with access token (for model downloads)

### Install Gateway API Components

```bash
# 1. Install GAIE CRDs
oc apply -k https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd/?ref=v1.3.0

# 2. Install agentgateway CRDs + control plane
# This is safe on OpenShift 4.19 - does NOT install gateway.networking.k8s.io CRDs
helmfile apply -f guides/prereq/gateway-provider/agentgateway.helmfile.yaml
```

> **Note**: If `guides/` directory is not available, install agentgateway manually via Helm. See [llm-d documentation](https://llm-d.ai/docs/guide/Installation/prerequisites/).

## Deployment

### Step 1: Environment Setup

```bash
export NAMESPACE=llm-d
export MODEL_SERVER=vllm
export IGW_CHART_VERSION=v1.3.0
export HF_TOKEN=<your-huggingface-token>

# Create namespace
oc create namespace ${NAMESPACE} || true

# Create HuggingFace token secret
oc create secret generic llm-d-hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  --namespace ${NAMESPACE}
```

### Step 2: Create PersistentVolumeClaims

Create PVCs for model storage (RWO volumes):

```bash
oc apply -f manifests/qwen35-35b-a3b/pvc.yaml
oc apply -f manifests/qwen35-27b-distilled/pvc.yaml
oc apply -f manifests/qwen3-next-80b/pvc.yaml
oc apply -f manifests/qwen35-9b/pvc.yaml
```

Verify:

```bash
oc get pvc -n ${NAMESPACE}
```

### Step 3: Download Models to PVCs

Launch download pods to populate PVCs with models from HuggingFace:

```bash
oc apply -f manifests/qwen35-35b-a3b/download.yaml
oc apply -f manifests/qwen35-27b-distilled/download.yaml
oc apply -f manifests/qwen3-next-80b/download.yaml
oc apply -f manifests/qwen35-9b/download.yaml
```

Wait for all downloads to complete (can take 30-120 minutes depending on network):

```bash
oc wait --for=condition=Ready \
  pod/download-qwen35-35b-a3b \
  pod/download-qwen35-27b-distilled \
  pod/download-qwen3-next-80b \
  pod/download-qwen35-9b \
  -n ${NAMESPACE} \
  --timeout=7200s
```

Monitor download progress:

```bash
# Check logs for any download pod
oc logs -f download-qwen3-next-80b -n ${NAMESPACE}
```

**Important**: Delete download pods after completion to release RWO PVCs:

```bash
oc delete pod download-qwen35-35b-a3b download-qwen35-27b-distilled download-qwen3-next-80b download-qwen35-9b -n ${NAMESPACE}
```

### Step 4: Deploy Gateway Infrastructure

```bash
# Deploy agentgateway Gateway
oc apply -f manifests/shared/gateway.yaml

# Apply SCC binding for non-root vLLM containers
oc apply -f manifests/shared/scc-binding.yaml
```

Wait for agentgateway data plane to be running:

```bash
oc rollout status deployment -n agentgateway-system
```

> **Why wait?** The `AgentgatewayPolicy` CRD is registered only after agentgateway control plane is fully initialized.

Apply body-based routing policy:

```bash
oc apply -f manifests/shared/agentgateway-policy.yaml
```

### Step 5: Deploy InferencePools

InferencePools create the connection between Gateway HTTPRoutes and model server pods via label selectors.

```bash
# Add llm-d-modelservice Helm repository
helm repo add llm-d-modelservice https://llm-d-incubation.github.io/llm-d-modelservice/
helm repo update

# Deploy InferencePool for Qwen3.5-35B-A3B
helm upgrade --install ${MODEL_SERVER}-qwen35-35b-a3b \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=${MODEL_SERVER}-qwen35-35b-a3b \
  --set provider.name=none \
  --set inferencePool.modelServerType=${MODEL_SERVER} \
  --set experimentalHttpRoute.enabled=true \
  --set experimentalHttpRoute.baseModel="Qwen/Qwen3.5-35B-A3B" \
  --version ${IGW_CHART_VERSION} \
  --namespace ${NAMESPACE} \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool

# Deploy InferencePool for Qwen3.5-27B-distilled
helm upgrade --install ${MODEL_SERVER}-qwen35-27b-distilled \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=${MODEL_SERVER}-qwen35-27b-distilled \
  --set provider.name=none \
  --set inferencePool.modelServerType=${MODEL_SERVER} \
  --set experimentalHttpRoute.enabled=true \
  --set experimentalHttpRoute.baseModel="Jackrong/Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled" \
  --version ${IGW_CHART_VERSION} \
  --namespace ${NAMESPACE} \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool

# Deploy InferencePool for Qwen3-Next-80B
helm upgrade --install ${MODEL_SERVER}-qwen3-next-80b \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=${MODEL_SERVER}-qwen3-next-80b \
  --set provider.name=none \
  --set inferencePool.modelServerType=${MODEL_SERVER} \
  --set experimentalHttpRoute.enabled=true \
  --set experimentalHttpRoute.baseModel="Qwen/Qwen3-Next-80B-A3B-Thinking" \
  --version ${IGW_CHART_VERSION} \
  --namespace ${NAMESPACE} \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool

# Deploy InferencePool for Qwen3.5-9B
helm upgrade --install ${MODEL_SERVER}-qwen35-9b \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=${MODEL_SERVER}-qwen35-9b \
  --set provider.name=none \
  --set inferencePool.modelServerType=${MODEL_SERVER} \
  --set experimentalHttpRoute.enabled=true \
  --set experimentalHttpRoute.baseModel="Qwen/Qwen3.5-9B" \
  --version ${IGW_CHART_VERSION} \
  --namespace ${NAMESPACE} \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool
```

### Step 6: Deploy Model Servers (vLLM)

Deploy llm-d-modelservice instances for each model:

```bash
helm upgrade --install ms-qwen35-35b-a3b llm-d-modelservice/llm-d-modelservice \
  --namespace ${NAMESPACE} \
  -f manifests/qwen35-35b-a3b/values.yaml

helm upgrade --install ms-qwen35-27b-distilled llm-d-modelservice/llm-d-modelservice \
  --namespace ${NAMESPACE} \
  -f manifests/qwen35-27b-distilled/values.yaml

helm upgrade --install ms-qwen3-next-80b llm-d-modelservice/llm-d-modelservice \
  --namespace ${NAMESPACE} \
  -f manifests/qwen3-next-80b/values.yaml

helm upgrade --install ms-qwen35-9b llm-d-modelservice/llm-d-modelservice \
  --namespace ${NAMESPACE} \
  -f manifests/qwen35-9b/values.yaml
```

Wait for all decode pods to become ready:

```bash
oc get pods -n ${NAMESPACE} -l llm-d.ai/role=decode -w
```

### Step 7: Patch HTTPRoutes

HTTPRoutes need to reference the Gateway namespace explicitly:

```bash
oc patch httproute ${MODEL_SERVER}-qwen35-35b-a3b -n ${NAMESPACE} --type='json' \
  -p='[{"op":"add","path":"/spec/parentRefs/0/namespace","value":"agentgateway-system"}]'

oc patch httproute ${MODEL_SERVER}-qwen35-27b-distilled -n ${NAMESPACE} --type='json' \
  -p='[{"op":"add","path":"/spec/parentRefs/0/namespace","value":"agentgateway-system"}]'

oc patch httproute ${MODEL_SERVER}-qwen3-next-80b -n ${NAMESPACE} --type='json' \
  -p='[{"op":"add","path":"/spec/parentRefs/0/namespace","value":"agentgateway-system"}]'

oc patch httproute ${MODEL_SERVER}-qwen35-9b -n ${NAMESPACE} --type='json' \
  -p='[{"op":"add","path":"/spec/parentRefs/0/namespace","value":"agentgateway-system"}]'
```

### Step 8: Expose Gateway via OpenShift Route

```bash
oc apply -f manifests/shared/route.yaml
```

Get the external URL:

```bash
GATEWAY_URL=$(oc get route inference-gateway -n agentgateway-system -o jsonpath='{.spec.host}')
echo "Gateway URL: https://${GATEWAY_URL}"
```

## Verification

### Check Deployment Status

```bash
# View all pods
oc get pods -n ${NAMESPACE}

# Check InferencePools and HTTPRoutes
oc get inferencepool,httproute -n ${NAMESPACE}

# Verify model server readiness
oc get pods -n ${NAMESPACE} -l llm-d.ai/role=decode -o wide
```

Expected output:

```text
NAME                                                    READY   STATUS
ms-qwen35-35b-a3b-llm-d-modelservice-decode-xxx         1/1     Running
ms-qwen35-27b-distilled-llm-d-modelservice-decode-xxx   1/1     Running
ms-qwen3-next-80b-llm-d-modelservice-decode-xxx         1/1     Running
ms-qwen35-9b-llm-d-modelservice-decode-xxx              1/1     Running
```

### List Deployed Models

**Note**: `GET /v1/models` returns 404 through the gateway because the AgentgatewayPolicy CEL expression requires a JSON body with a `model` field for routing, which GET requests don't have.

Use the cluster API instead:

```bash
# List InferencePools (one per model)
oc get inferencepool -n llm-d

# Query all model servers directly
for app in vllm-qwen35-35b-a3b vllm-qwen35-27b-distilled vllm-qwen3-next-80b vllm-qwen35-9b; do
  echo "=== ${app} ==="
  oc get pods -n llm-d -l app=${app} -o name | head -1 | \
    xargs -I{} oc exec {} -n llm-d -c vllm -- curl -s http://localhost:8200/v1/models | jq -r '.data[].id'
done
```

### Test Inference

Test each model through the gateway:

```bash
# Qwen3.5-35B-A3B
curl -s -X POST https://${GATEWAY_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3.5-35B-A3B",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }' | jq .

# Qwen3.5-27B distilled
curl -s -X POST https://${GATEWAY_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Jackrong/Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }' | jq .

# Qwen3-Next-80B FP8
curl -s -X POST https://${GATEWAY_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-Next-80B-A3B-Thinking",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }' | jq .

# Qwen3.5-9B
curl -s -X POST https://${GATEWAY_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3.5-9B",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }' | jq .
```

## Embedding Model (TEI)

### Deploy Text Embeddings Inference

The `sentence-transformers/all-mpnet-base-v2` model is **not deployable via vLLM** (MPNetModel architecture is unsupported). It runs on HuggingFace TEI as a standalone CPU deployment.

```bash
# Create PVC for model persistence
oc apply -f manifests/all-mpnet-base-v2/pvc.yaml

# Deploy TEI embedding service
oc apply -f manifests/all-mpnet-base-v2/deployment.yaml

# Wait for rollout
oc rollout status deployment/tei-all-mpnet-base-v2 -n ${NAMESPACE}

# Expose externally
oc apply -f manifests/all-mpnet-base-v2/route.yaml
```

### Test Embeddings

```bash
TEI_URL=$(oc get route tei-all-mpnet-base-v2 -n ${NAMESPACE} -o jsonpath='{.spec.host}')

# OpenAI-compatible endpoint
curl -s -X POST https://${TEI_URL}/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "sentence-transformers/all-mpnet-base-v2",
    "input": "Hello world"
  }' | jq .

# TEI-native endpoint (returns float arrays directly)
curl -s -X POST https://${TEI_URL}/embed \
  -H "Content-Type: application/json" \
  -d '{"inputs": "Hello world"}' | jq .
```

## Troubleshooting

### Common Issues

#### 1. Prefill pods showing "max context length 1024" errors

**Symptom**: Logs show `max context length is 1024 tokens` errors.

**Cause**: Prefill pods are running the `llm-d-inference-sim` (simulator) instead of vLLM.

**Fix**: Ensure all model values files have `prefill: { create: false }`. This disables P/D disaggregation (not needed for single-GPU deployments).

#### 2. vLLM permission errors on OpenShift

**Symptom**: Logs show errors like `/.triton: Permission denied` or `/.cache: Permission denied`.

**Cause**: OpenShift runs containers as non-root (random UID). vLLM tries to write to home directory.

**Fix**: Set `HOME=/tmp` in container environment (already configured in values files).

#### 3. RWO PVC claim failures

**Symptom**: Model server pods stuck in `Pending` with "PVC already in use" error.

**Cause**: Download pod still claims the RWO PVC.

**Fix**: Delete download pods after model downloads complete:

```bash
oc delete pod download-qwen3-next-80b -n ${NAMESPACE}
```

#### 4. HTTPRoute not routing traffic

**Symptom**: Requests return 404 or "no matching route".

**Cause**: HTTPRoute missing `parentRefs[0].namespace` field.

**Fix**: Apply the patch from Step 7:

```bash
oc patch httproute vllm-qwen3-next-80b -n ${NAMESPACE} --type='json' \
  -p='[{"op":"add","path":"/spec/parentRefs/0/namespace","value":"agentgateway-system"}]'
```

### Debug Commands

```bash
# Check vLLM logs
oc logs -f deployment/ms-qwen3-next-80b-llm-d-modelservice-decode -c vllm -n ${NAMESPACE}

# Check Gateway routing
oc describe httproute vllm-qwen3-next-80b -n ${NAMESPACE}

# Check InferencePool status
oc describe inferencepool vllm-qwen3-next-80b -n ${NAMESPACE}

# Test direct pod access (bypass gateway)
POD=$(oc get pods -n ${NAMESPACE} -l app=vllm-qwen3-next-80b -o name | head -1)
oc exec ${POD} -n ${NAMESPACE} -c vllm -- curl -s http://localhost:8200/v1/models | jq .
```

## Configuration Details

### Model Server Configuration

All model servers use these vLLM arguments:

- `--enable-auto-tool-choice` - Auto-detect tool calling
- `--tool-call-parser hermes` - Use Hermes tool format
- `--disable-access-log-for-endpoints /health,/metrics,/ping` - Reduce log noise
- `--trust-remote-code` - Allow custom model code (required for some models)
- `--max-model-len 32768` - Set 32K context window (where applicable)

### Resource Allocation

Each decode pod requests:

- **1x NVIDIA GPU** (`nvidia.com/gpu: 1`)
- **HOME=/tmp** environment variable for non-root compatibility

### Chart Versions

- **llm-d-modelservice**: v0.4.8
- **Gateway API Inference Extension**: v1.3.0
- **vLLM image**: v0.17.0 (minimum for Qwen3.5 support)

## Cleanup

### Remove All Deployments

```bash
export NAMESPACE=llm-d
export MODEL_SERVER=vllm

# Delete model servers
helm uninstall ms-qwen35-35b-a3b -n ${NAMESPACE}
helm uninstall ms-qwen35-27b-distilled -n ${NAMESPACE}
helm uninstall ms-qwen3-next-80b -n ${NAMESPACE}
helm uninstall ms-qwen35-9b -n ${NAMESPACE}

# Delete InferencePools
helm uninstall ${MODEL_SERVER}-qwen35-35b-a3b -n ${NAMESPACE}
helm uninstall ${MODEL_SERVER}-qwen35-27b-distilled -n ${NAMESPACE}
helm uninstall ${MODEL_SERVER}-qwen3-next-80b -n ${NAMESPACE}
helm uninstall ${MODEL_SERVER}-qwen35-9b -n ${NAMESPACE}

# Delete embedding service
oc delete -f manifests/all-mpnet-base-v2/

# Delete Gateway resources
oc delete -f manifests/shared/

# Delete PVCs (WARNING: deletes downloaded models)
oc delete pvc qwen35-35b-a3b-pvc qwen35-27b-distilled-pvc qwen3-next-80b-pvc qwen35-9b-pvc all-mpnet-base-v2-pvc -n ${NAMESPACE}

# Delete namespace
oc delete namespace ${NAMESPACE}
```

### Keep PVCs (Fast Re-deployment)

To keep downloaded models but remove running services:

```bash
# Stop model servers only
helm uninstall ms-qwen35-35b-a3b ms-qwen35-27b-distilled ms-qwen3-next-80b ms-qwen35-9b -n ${NAMESPACE}

# PVCs remain - next deployment will skip downloads
```

## References

- [llm-d Documentation](https://llm-d.ai/)
- [llm-d-modelservice Chart](https://llm-d-incubation.github.io/llm-d-modelservice/)
- [Gateway API Inference Extension](https://github.com/kubernetes-sigs/gateway-api-inference-extension)
- [vLLM Documentation](https://docs.vllm.ai/)
- [Qwen Models on HuggingFace](https://huggingface.co/Qwen)

## License

These deployment manifests are provided as-is for reference. Individual components (llm-d, vLLM, models) have their own licenses.

## Contributing

Issues and improvements welcome! Please test changes on a non-production cluster first.
