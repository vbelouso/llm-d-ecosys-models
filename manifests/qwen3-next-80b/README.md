# Qwen/Qwen3-Next-80B-A3B-Thinking

**HuggingFace**: <https://huggingface.co/Qwen/Qwen3-Next-80B-A3B-Thinking>

## Deployment

### Prerequisites

```bash
export NAMESPACE=llm-d
export MODEL_SERVER=vllm
export IGW_CHART_VERSION=v1.3.0
```

### 1. Create PVC

```bash
oc apply -f manifests/qwen3-next-80b/pvc.yaml
```

Verify:

```bash
oc get pvc qwen3-next-80b-pvc -n ${NAMESPACE}
```

### 2. Download Model

```bash
oc apply -f manifests/qwen3-next-80b/download.yaml
```

Wait for completion (60-120 minutes):

```bash
oc wait --for=condition=Ready pod/download-qwen3-next-80b -n ${NAMESPACE} --timeout=7200s
```

Monitor progress:

```bash
oc logs -f download-qwen3-next-80b -n ${NAMESPACE}
```

**Important**: Delete download pod after completion:

```bash
oc delete pod download-qwen3-next-80b -n ${NAMESPACE}
```

### 3. Deploy InferencePool

```bash
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
```

### 4. Deploy Model Server

```bash
helm upgrade --install ms-qwen3-next-80b llm-d-modelservice/llm-d-modelservice \
  -f manifests/qwen3-next-80b/values.yaml \
  -n ${NAMESPACE}
```

### 5. Patch HTTPRoute

```bash
oc patch httproute ${MODEL_SERVER}-qwen3-next-80b -n ${NAMESPACE} --type='json' \
  -p='[{"op":"add","path":"/spec/parentRefs/0/namespace","value":"agentgateway-system"}]'
```

Verify:

```bash
oc get httproute ${MODEL_SERVER}-qwen3-next-80b -n ${NAMESPACE} -o jsonpath='{.spec.parentRefs[0].namespace}'
# Should output: agentgateway-system
```

### 6. Update AgentgatewayPolicy

Add the model to the routing policy (if not already present):

```bash
oc get agentgatewaypolicy bbr -n agentgateway-system -o yaml
```

Ensure `"Qwen/Qwen3-Next-80B-A3B-Thinking"` is in the model mapping.

## Testing

### 1. Check Pod Status

```bash
oc get pods -n ${NAMESPACE} -l app=${MODEL_SERVER}-qwen3-next-80b
```

Expected output:

```
NAME                                                      READY   STATUS    RESTARTS   AGE
ms-qwen3-next-80b-llm-d-modelservice-decode-xxxxx-xxxxx  1/1     Running   0          5m
```

### 2. Query Model Directly

```bash
POD=$(oc get pods -n ${NAMESPACE} -l app=${MODEL_SERVER}-qwen3-next-80b -o name | head -1)
oc exec ${POD} -c vllm -- curl -s http://localhost:8200/v1/models | jq .
```

Expected output:

```json
{
  "data": [
    {
      "id": "Qwen/Qwen3-Next-80B-A3B-Thinking",
      "object": "model",
      ...
    }
  ]
}
```

### 3. Test Inference via Gateway

Set Gateway URL:

```bash
export GATEWAY_URL=$(oc get route inference-gateway -n agentgateway-system -o jsonpath='{.spec.host}')
```

Simple inference:

```bash
curl -s -X POST https://${GATEWAY_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-Next-80B-A3B-Thinking",
    "messages": [{"role": "user", "content": "Solve this problem step by step: If x + 2 = 5, what is x?"}],
    "max_tokens": 200
  }' | jq .
```

### 4. Test Tool Calling

```bash
curl -s -X POST https://${GATEWAY_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-Next-80B-A3B-Thinking",
    "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "get_weather",
          "description": "Get current weather for a location",
          "parameters": {
            "type": "object",
            "properties": {
              "location": {"type": "string", "description": "City name"},
              "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
            },
            "required": ["location"]
          }
        }
      }
    ],
    "tool_choice": "auto"
  }' | jq .
```

## Troubleshooting

### Pod Not Starting

Check vLLM logs:

```bash
oc logs -f deployment/ms-qwen3-next-80b-llm-d-modelservice-decode -c vllm -n ${NAMESPACE}
```

Common issues:

- **PVC already in use**: Delete download pod first
- **Permission denied /.triton**: Missing `HOME=/tmp` env var (already configured in values.yaml)
- **OOM errors**: FP8 80B model requires significant GPU memory

### 404 Route Not Found

Verify HTTPRoute patch:

```bash
oc get httproute ${MODEL_SERVER}-qwen3-next-80b -n ${NAMESPACE} -o yaml | grep -A5 parentRefs
```

Check AgentgatewayPolicy includes the model:

```bash
oc get agentgatewaypolicy bbr -n agentgateway-system -o jsonpath='{.spec.traffic.transformation.request.set[0].value}' | grep -i qwen3-next
```

## Cleanup

```bash
# Delete model server
helm uninstall ms-qwen3-next-80b -n ${NAMESPACE}

# Delete InferencePool
helm uninstall ${MODEL_SERVER}-qwen3-next-80b -n ${NAMESPACE}

# Delete PVC (WARNING: deletes downloaded model)
oc delete pvc qwen3-next-80b-pvc -n ${NAMESPACE}
```
