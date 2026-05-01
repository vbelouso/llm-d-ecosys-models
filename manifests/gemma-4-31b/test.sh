#!/bin/bash
export GATEWAY_URL=$(oc get route inference-gateway -n agentgateway-system -o jsonpath='{.spec.host}')

curl -s -X POST https://${GATEWAY_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "RedHatAI/gemma-4-31B-it-FP8-block",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }' | jq .
