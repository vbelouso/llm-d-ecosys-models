#!/bin/bash
export GATEWAY_URL=$(oc get route inference-gateway -n agentgateway-system -o jsonpath='{.spec.host}')

curl -s -X POST https://${GATEWAY_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Jackrong/Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }' | jq .
