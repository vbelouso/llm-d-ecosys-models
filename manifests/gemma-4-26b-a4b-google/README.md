# google/gemma-4-26B-A4B-it

**HuggingFace**: <https://huggingface.co/google/gemma-4-26B-A4B-it>

**Model Size**: ~60 GB (FP16)

**Tool Calling**: Yes (gemma4 parser)

**vLLM Version**: gemma4 image (vllm/vllm-openai:gemma4)

## Deployment

### Prerequisites

```bash
export NAMESPACE=llm-d
export MODEL_SERVER=vllm
export IGW_CHART_VERSION=v1.3.0
```

### 1. Create PVC

```bash
oc apply -f manifests/gemma-4-26b-a4b-google/pvc.yaml
```

Verify:

```bash
oc get pvc gemma-4-26b-a4b-google-pvc -n ${NAMESPACE}
```

### 2. Download Model

```bash
oc apply -f manifests/gemma-4-26b-a4b-google/download.yaml
```

Wait for completion (30-60 minutes for 60GB):

```bash
oc wait --for=condition=Ready pod/download-gemma-4-26b-a4b-google -n ${NAMESPACE} --timeout=7200s
```

Monitor progress:

```bash
oc logs -f download-gemma-4-26b-a4b-google -n ${NAMESPACE}
```

**Important**: Delete download pod after completion to release RWO PVC:

```bash
oc delete pod download-gemma-4-26b-a4b-google -n ${NAMESPACE}
```

### 3. Deploy InferencePool

```bash
helm upgrade --install ${MODEL_SERVER}-gemma-4-26b-a4b-google \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=${MODEL_SERVER}-gemma-4-26b-a4b-google \
  --set provider.name=none \
  --set inferencePool.modelServerType=${MODEL_SERVER} \
  --set experimentalHttpRoute.enabled=true \
  --set experimentalHttpRoute.baseModel="google/gemma-4-26B-A4B-it" \
  --version ${IGW_CHART_VERSION} \
  --namespace ${NAMESPACE} \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool
```

### 4. Deploy Model Server

```bash
helm upgrade --install ms-gemma-4-26b-a4b-google llm-d-modelservice/llm-d-modelservice \
  -f manifests/gemma-4-26b-a4b-google/values.yaml \
  -n ${NAMESPACE}
```

### 5. Patch HTTPRoute

```bash
oc patch httproute ${MODEL_SERVER}-gemma-4-26b-a4b-google -n ${NAMESPACE} --type='json' \
  -p='[{"op":"add","path":"/spec/parentRefs/0/namespace","value":"agentgateway-system"}]'
```

Verify:

```bash
oc get httproute ${MODEL_SERVER}-gemma-4-26b-a4b-google -n ${NAMESPACE} -o jsonpath='{.spec.parentRefs[0].namespace}'
# Should output: agentgateway-system
```

### 6. Update AgentgatewayPolicy

Apply the updated routing policy that includes the Gemma model:

```bash
oc apply -f manifests/shared/agentgateway-policy.yaml
```

Verify the model is in the routing map:

```bash
oc get agentgatewaypolicy bbr -n agentgateway-system -o jsonpath='{.spec.traffic.transformation.request.set[0].value}' | grep "google/gemma-4-26B-A4B-it"
```

Expected output should include: `"google/gemma-4-26B-A4B-it"`

## Testing

### 1. Check Pod Status

```bash
oc get pods -n ${NAMESPACE} -l app=${MODEL_SERVER}-gemma-4-26b-a4b-google
```

Expected output:

```text
NAME                                                               READY   STATUS    RESTARTS   AGE
ms-gemma-4-26b-a4b-google-llm-d-modelservice-decode-xxxxx-xxxxx   1/1     Running   0          5m
```

### 2. Query Model Directly

```bash
POD=$(oc get pods -n ${NAMESPACE} -l app=${MODEL_SERVER}-gemma-4-26b-a4b-google -o name | head -1)
oc exec ${POD} -c vllm -- curl -s http://localhost:8200/v1/models | jq .
```

Expected output:

```json
{
  "data": [
    {
      "id": "google/gemma-4-26B-A4B-it",
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
    "model": "google/gemma-4-26B-A4B-it",
    "messages": [{"role": "user", "content": "Hello, what can you do?"}],
    "max_tokens": 100
  }' | jq .
```

### 4. Test Tool Calling

```bash
curl -s -X POST https://${GATEWAY_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "google/gemma-4-26B-A4B-it",
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

Expected response includes:

```json
{
  "choices": [
    {
      "finish_reason": "tool_calls",
      "message": {
        "tool_calls": [
          {
            "function": {
              "name": "get_current_weather",
              "arguments": "{\"city\": \"Boston\"}"
            }
          }
        ]
      }
    }
  ]
}
```

## Troubleshooting

### Model Loading Errors

Check vLLM logs for errors:

```bash
oc logs -f deployment/ms-gemma-4-26b-a4b-google-llm-d-modelservice-decode -c vllm -n ${NAMESPACE}
```

### Pod Not Starting

Common issues:

- **PVC already in use**: Delete download pod first
- **Permission denied /.triton**: Missing `HOME=/tmp` env var (already configured in values.yaml)
- **OOM errors**: Model requires ~40GB VRAM; scale down other models if needed

### 404 Route Not Found

Verify HTTPRoute patch:

```bash
oc get httproute ${MODEL_SERVER}-gemma-4-26b-a4b-google -n ${NAMESPACE} -o yaml | grep -A5 parentRefs
```

Check AgentgatewayPolicy includes the model:

```bash
oc get agentgatewaypolicy bbr -n agentgateway-system -o jsonpath='{.spec.traffic.transformation.request.set[0].value}' | grep "google/gemma-4-26B-A4B-it"
```

## Cleanup

```bash
# Delete model server
helm uninstall ms-gemma-4-26b-a4b-google -n ${NAMESPACE}

# Delete InferencePool
helm uninstall ${MODEL_SERVER}-gemma-4-26b-a4b-google -n ${NAMESPACE}

# Delete PVC (WARNING: deletes downloaded model)
oc delete pvc gemma-4-26b-a4b-google-pvc -n ${NAMESPACE}
```

## Notes

- **Model Architecture**: Gemma 4 A4B (Mixture of Experts with 26B params)
- **Context Length**: 262K tokens supported
- **Tool Parser**: Uses `gemma4` parser (vLLM gemma4 image)
- **Quantization**: None (FP16 base model) - no special patches required
