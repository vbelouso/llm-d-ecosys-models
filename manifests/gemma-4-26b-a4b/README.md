# RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic

**HuggingFace**: <https://huggingface.co/RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic>

**vLLM Version**: gemma4 image with patched weight loader for RedHatAI's flattened MoE checkpoint structure

**Note**: This model uses FP8-Dynamic quantization with a "flattened" checkpoint structure (missing `.moe.` prefix). A ConfigMap patches the vLLM weight loader to handle this format. See: <https://gist.github.com/lioreshai/07b2ccb2c69616504d01b25383dfe895>

## Deployment

### Prerequisites

```bash
export NAMESPACE=llm-d
export MODEL_SERVER=vllm
export IGW_CHART_VERSION=v1.3.0
```

### 1. Apply vLLM Weight Loader Patch

**This step is REQUIRED for RedHatAI FP8-Dynamic models.**

Extract and patch gemma4.py from a running vLLM pod, then create ConfigMap:

```bash
# Extract gemma4.py from running vllm pod
POD=$(oc get pods -n ${NAMESPACE} -l app=vllm-gemma-4-31b -o name | head -1)
oc exec -n ${NAMESPACE} ${POD} -c vllm -- \
  cat /usr/local/lib/python3.12/dist-packages/vllm/model_executor/models/gemma4.py > /tmp/gemma4.py.orig

# Apply RedHatAI flattened MoE patch (see https://gist.github.com/lioreshai/07b2ccb2c69616504d01b25383dfe895)
# Patch adds .moe. prefix handling for RedHatAI's flattened checkpoint structure
python3 manifests/gemma-4-26b-a4b/patch-gemma4.py /tmp/gemma4.py.orig /tmp/gemma4.py.patched

# Create ConfigMap
oc create configmap gemma4-patched --from-file=gemma4.py=/tmp/gemma4.py.patched -n ${NAMESPACE}
```

**Note**: The `values.yaml` mounts this ConfigMap over the original gemma4.py to apply the patch at runtime.

### 2. Create PVC

```bash
oc apply -f manifests/gemma-4-26b-a4b/pvc.yaml
```

Verify:

```bash
oc get pvc gemma-4-26b-a4b-pvc -n ${NAMESPACE}
```

### 3. Download Model

```bash
oc apply -f manifests/gemma-4-26b-a4b/download.yaml
```

Wait for completion (30-60 minutes):

```bash
oc wait --for=condition=Ready pod/download-gemma-4-26b-a4b -n ${NAMESPACE} --timeout=7200s
```

Monitor progress:

```bash
oc logs -f download-gemma-4-26b-a4b -n ${NAMESPACE}
```

**Important**: Delete download pod after completion:

```bash
oc delete pod download-gemma-4-26b-a4b -n ${NAMESPACE}
```

### 4. Deploy InferencePool

```bash
helm upgrade --install ${MODEL_SERVER}-gemma-4-26b-a4b \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=${MODEL_SERVER}-gemma-4-26b-a4b \
  --set provider.name=none \
  --set inferencePool.modelServerType=${MODEL_SERVER} \
  --set experimentalHttpRoute.enabled=true \
  --set experimentalHttpRoute.baseModel="RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic" \
  --version ${IGW_CHART_VERSION} \
  --namespace ${NAMESPACE} \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool
```

### 5. Deploy Model Server

```bash
helm upgrade --install ms-gemma-4-26b-a4b llm-d-modelservice/llm-d-modelservice \
  -f manifests/gemma-4-26b-a4b/values.yaml \
  -n ${NAMESPACE}
```

### 6. Patch HTTPRoute

```bash
oc patch httproute ${MODEL_SERVER}-gemma-4-26b-a4b -n ${NAMESPACE} --type='json' \
  -p='[{"op":"add","path":"/spec/parentRefs/0/namespace","value":"agentgateway-system"}]'
```

Verify:

```bash
oc get httproute ${MODEL_SERVER}-gemma-4-26b-a4b -n ${NAMESPACE} -o jsonpath='{.spec.parentRefs[0].namespace}'
# Should output: agentgateway-system
```

### 7. Update AgentgatewayPolicy

Add the model to the routing policy (if not already present):

```bash
oc get agentgatewaypolicy bbr -n agentgateway-system -o yaml
```

Ensure `"RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic"` is in the model mapping.

## Testing

### 1. Check Pod Status

```bash
oc get pods -n ${NAMESPACE} -l app=${MODEL_SERVER}-gemma-4-26b-a4b
```

Expected output:

```text
NAME                                                         READY   STATUS    RESTARTS   AGE
ms-gemma-4-26b-a4b-llm-d-modelservice-decode-xxxxx-xxxxx    1/1     Running   0          5m
```

### 2. Query Model Directly

```bash
POD=$(oc get pods -n ${NAMESPACE} -l app=${MODEL_SERVER}-gemma-4-26b-a4b -o name | head -1)
oc exec ${POD} -c vllm -- curl -s http://localhost:8200/v1/models | jq .
```

Expected output:

```json
{
  "data": [
    {
      "id": "RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic",
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
    "model": "RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }' | jq .
```

### 4. Test Tool Calling

```bash
curl -s -X POST https://${GATEWAY_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic",
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
              "name": "get_weather",
              "arguments": "{\"location\": \"Paris\"}"
            }
          }
        ]
      }
    }
  ]
}
```

## Troubleshooting

### Pod Not Starting

Check vLLM logs:

```bash
oc logs -f deployment/ms-gemma-4-26b-a4b-llm-d-modelservice-decode -c vllm -n ${NAMESPACE}
```

Common issues:

- **PVC already in use**: Delete download pod first
- **Permission denied /.triton**: Missing `HOME=/tmp` env var (already configured in values.yaml)

### 404 Route Not Found

Verify HTTPRoute patch:

```bash
oc get httproute ${MODEL_SERVER}-gemma-4-26b-a4b -n ${NAMESPACE} -o yaml | grep -A5 parentRefs
```

Check AgentgatewayPolicy includes the model:

```bash
oc get agentgatewaypolicy bbr -n agentgateway-system -o jsonpath='{.spec.traffic.transformation.request.set[0].value}' | grep gemma-4-26B-A4B-it-FP8-Dynamic
```

## Cleanup

```bash
# Delete model server
helm uninstall ms-gemma-4-26b-a4b -n ${NAMESPACE}

# Delete InferencePool
helm uninstall ${MODEL_SERVER}-gemma-4-26b-a4b -n ${NAMESPACE}

# Delete PVC (WARNING: deletes downloaded model)
oc delete pvc gemma-4-26b-a4b-pvc -n ${NAMESPACE}
```
