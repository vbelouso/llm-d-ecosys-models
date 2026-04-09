# sentence-transformers/all-mpnet-base-v2

**HuggingFace**: <https://huggingface.co/sentence-transformers/all-mpnet-base-v2>

## Deployment

```bash
export NAMESPACE=llm-d

# Create PVC
oc apply -f manifests/all-mpnet-base-v2/pvc.yaml

# Deploy TEI
oc apply -f manifests/all-mpnet-base-v2/deployment.yaml
oc rollout status deployment/tei-all-mpnet-base-v2 -n ${NAMESPACE}

# Expose route
oc apply -f manifests/all-mpnet-base-v2/route.yaml
```

## Testing

```bash
TEI_URL=$(oc get route tei-all-mpnet-base-v2 -n ${NAMESPACE} -o jsonpath='{.spec.host}')

# OpenAI-compatible endpoint
curl -s -X POST https://${TEI_URL}/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "sentence-transformers/all-mpnet-base-v2",
    "input": "Hello world"
  }' | jq .

# TEI-native endpoint
curl -s -X POST https://${TEI_URL}/embed \
  -H "Content-Type: application/json" \
  -d '{"inputs": "Hello world"}' | jq .
```

## Cleanup

```bash
oc delete -f manifests/all-mpnet-base-v2/
oc delete pvc all-mpnet-base-v2-pvc -n ${NAMESPACE}
```
