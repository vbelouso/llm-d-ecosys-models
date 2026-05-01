# ibm-granite/granite-4.1-30b-fp8

**HuggingFace**: <https://huggingface.co/ibm-granite/granite-4.1-30b-fp8>

**Model Size**: ~30 GB (FP8)

**Tool Calling**: Yes (granite4 parser)

**Architecture**: Dense decoder-only (GraniteForCausalLM, 30B params)

## Deployment

### Prerequisites

```bash
export NAMESPACE=llm-d
export MODEL_SERVER=vllm
export IGW_CHART_VERSION=v1.3.0
```

### 1. Create PVC

```bash
oc apply -f manifests/granite-41-30b-fp8/pvc.yaml
```

Verify:

```bash
oc get pvc granite-41-30b-fp8-pvc -n ${NAMESPACE}
```

### 2. Download Model

```bash
oc apply -f manifests/granite-41-30b-fp8/download.yaml
```

Wait for completion (20-40 minutes for 30GB):

```bash
oc wait --for=condition=Ready pod/download-granite-41-30b-fp8 -n ${NAMESPACE} --timeout=7200s
```

Monitor progress:

```bash
oc logs -f download-granite-41-30b-fp8 -n ${NAMESPACE}
```

**Important**: Delete download pod after completion to release RWO PVC:

```bash
oc delete pod download-granite-41-30b-fp8 -n ${NAMESPACE}
```

### 3. Deploy InferencePool

```bash
helm upgrade --install ${MODEL_SERVER}-granite-41-30b-fp8 \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=${MODEL_SERVER}-granite-41-30b-fp8 \
  --set provider.name=none \
  --set inferencePool.modelServerType=${MODEL_SERVER} \
  --set experimentalHttpRoute.enabled=true \
  --set experimentalHttpRoute.baseModel="ibm-granite/granite-4.1-30b-fp8" \
  --version ${IGW_CHART_VERSION} \
  --namespace ${NAMESPACE} \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool
```

### 4. Deploy Model Server

```bash
helm upgrade --install ms-granite-41-30b-fp8 llm-d-modelservice/llm-d-modelservice \
  -f manifests/granite-41-30b-fp8/values.yaml \
  -n ${NAMESPACE}
```

### 5. Patch HTTPRoute

```bash
oc patch httproute ${MODEL_SERVER}-granite-41-30b-fp8 -n ${NAMESPACE} --type='json' \
  -p='[{"op":"add","path":"/spec/parentRefs/0/namespace","value":"agentgateway-system"}]'
```

Verify:

```bash
oc get httproute ${MODEL_SERVER}-granite-41-30b-fp8 -n ${NAMESPACE} -o jsonpath='{.spec.parentRefs[0].namespace}'
# Should output: agentgateway-system
```

### 6. Update AgentgatewayPolicy

Apply the updated routing policy that includes the Granite model:

```bash
oc apply -f manifests/shared/agentgateway-policy.yaml
```

Verify the model is in the routing map:

```bash
oc get agentgatewaypolicy bbr -n agentgateway-system -o jsonpath='{.spec.traffic.transformation.request.set[0].value}' | grep granite
```

Expected output should include: `"ibm-granite/granite-4.1-30b-fp8"`

## Testing

### 1. Check Pod Status

```bash
oc get pods -n ${NAMESPACE} -l app=${MODEL_SERVER}-granite-41-30b-fp8
```

### 2. Query Model Directly

```bash
POD=$(oc get pods -n ${NAMESPACE} -l app=${MODEL_SERVER}-granite-41-30b-fp8 -o name | head -1)
oc exec ${POD} -c vllm -- curl -s http://localhost:8200/v1/models | jq .
```

### 3. Test Inference via Gateway

```bash
export GATEWAY_URL=$(oc get route inference-gateway -n agentgateway-system -o jsonpath='{.spec.host}')

curl -s -X POST https://${GATEWAY_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "ibm-granite/granite-4.1-30b-fp8",
    "messages": [{"role": "user", "content": "Hello, what can you do?"}],
    "max_tokens": 100
  }' | jq .
```

### 4. Test Tool Calling

```bash
curl -s -X POST https://${GATEWAY_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "ibm-granite/granite-4.1-30b-fp8",
    "messages": [{"role": "user", "content": "What is the weather in Boston?"}],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "get_current_weather",
          "description": "Get the current weather for a specified city",
          "parameters": {
            "type": "object",
            "properties": {
              "city": {"type": "string", "description": "City name"}
            },
            "required": ["city"]
          }
        }
      }
    ],
    "tool_choice": "auto"
  }' | jq .
```

## Troubleshooting

### Model Loading Errors

```bash
oc logs -f deployment/ms-granite-41-30b-fp8-llm-d-modelservice-decode -c vllm -n ${NAMESPACE}
```

### Pod Not Starting

Common issues:

- **PVC already in use**: Delete download pod first
- **Permission denied /.triton**: Missing `HOME=/tmp` env var (already configured in values.yaml)
- **OOM errors**: Model requires ~30-40GB VRAM; ensure GPU has sufficient memory

### 404 Route Not Found

```bash
oc get httproute ${MODEL_SERVER}-granite-41-30b-fp8 -n ${NAMESPACE} -o yaml | grep -A5 parentRefs
```

## Cleanup

```bash
helm uninstall ms-granite-41-30b-fp8 -n ${NAMESPACE}
helm uninstall ${MODEL_SERVER}-granite-41-30b-fp8 -n ${NAMESPACE}
oc delete pvc granite-41-30b-fp8-pvc -n ${NAMESPACE}
```

## Notes

- **Architecture**: Dense decoder-only transformer (30B params, GQA with 32 heads / 8 KV heads)
- **Context Length**: 131,072 tokens
- **Quantization**: FP8 (F8_E4M3) via compressed-tensors
- **Tool Parser**: Uses `granite4` parser (vLLM v0.19.0+)
- **Multilingual**: Supports 12 languages (EN, DE, ES, FR, JA, PT, AR, CS, IT, KO, NL, ZH)
