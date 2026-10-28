# Repository Agent Instructions

Use these instructions when changing manifests, operating the serving deployment, or updating documentation. The root [README](README.md) describes the current architecture and provides deployment commands.

## Project Scope

This repository contains model-serving manifests for Red Hat OpenShift. Chat inference uses llm-d, vLLM, Gateway API Inference Extension, and agentgateway. The embedding example uses a separate CPU-based Text Embeddings Inference (TEI) deployment.

Use repository manifests as the configuration source. Use cluster inspection to establish runtime state. Distinguish configured models, installed Helm releases, and ready model servers when reporting results.

## Repository Layout

The serving configuration is organized as follows:

| Path | Contents |
| --- | --- |
| `manifests/shared/gateway.yaml` | Shared Gateway and listener configuration |
| `manifests/shared/agentgateway-policy.yaml` | Model ID mapping used for body-based routing |
| `manifests/shared/route.yaml` | External OpenShift Route with edge TLS and a `7200s` router timeout |
| `manifests/shared/scc-binding.yaml` | Gateway service account binding to `nonroot-v2` |
| `manifests/<model-key>/` | Model PVC, download pod, Helm values, model-specific instructions, and request examples |
| `manifests/all-mpnet-base-v2/` | Separate TEI deployment, storage, and OpenShift Route for embeddings |
| `README.md` | Deployment procedure, architecture diagram, and verification commands |

## Serving Architecture

The current deployment uses llm-d Gateway Mode. Its request path has the following stages:

1. The OpenShift Route terminates client TLS and forwards HTTP traffic to the Gateway service on port `8080`.
2. The shared agentgateway applies policy `bbr` before routing. The policy reads the JSON `model` field and sets `X-Gateway-Base-Model-Name`.
3. A model-specific HTTPRoute matches that header and references an InferencePool.
4. The Gateway consults the pool's endpoint picker (EPP) on port `9002` and forwards inference to the selected model pod on port `8000`.

The Gateway, policy, Route, and Gateway SCC role binding reside in `agentgateway-system`. The model servers, InferencePools, EPPs, and HTTPRoutes reside in `llm-d`.

Use the following names and settings when inspecting resources:

| Resource | Name or invariant |
| --- | --- |
| Gateway and OpenShift Route | `inference-gateway` in `agentgateway-system` |
| GatewayClass | `agentgateway-v2` |
| AgentgatewayPolicy | `bbr` in `agentgateway-system` |
| Model-service Helm release | `ms-<model-key>` |
| Decode deployment | `ms-<model-key>-llm-d-modelservice-decode` |
| InferencePool Helm release and resource | `vllm-<model-key>` |
| EPP deployment | `vllm-<model-key>-epp` |
| Model pod label | `app=vllm-<model-key>`; must match the InferencePool selector |
| HTTPRoute parent | Gateway `inference-gateway`, namespace `agentgateway-system` |

Each active model has its own InferencePool and EPP. The Gateway is shared. Model-server replica counts and EPP replica counts are independent.

Both active models run prefill and decode in one vLLM process. Their values set `prefill.create: false`, `routing.proxy.enabled: false`, and `routing.servicePort: 8000`. Do not add the llm-d routing sidecar unless the task introduces a validated prefill/decode disaggregation design.

## Verified Deployment Baseline

The baseline was verified on 28 October 2026. Recheck live state before making operational decisions.

| Model key | Served model ID | Container image | PVC |
| --- | --- | --- | --- |
| `gemma-4-31b` | `RedHatAI/gemma-4-31B-it-FP8-block` | `vllm/vllm-openai:gemma4` | `gemma-4-31b-pvc`, 45 GiB |
| `granite-41-30b-fp8` | `ibm-granite/granite-4.1-30b-fp8` | `vllm/vllm-openai:v0.19.0` | `granite-41-30b-fp8-pvc`, 60 GiB |

Both model servers and both EPPs have one ready replica. Both models return HTTP 200 through the external Gateway. Other installed model servers and their EPPs are scaled to zero at the time of verification.

The active model-service releases use chart `v0.4.15`. InferencePool releases use chart `v1.3.0`. Older model-service releases still use chart `v0.4.9`; do not assume that the entire namespace has migrated.

The verified cluster runs OpenShift `4.20.13`, NVIDIA H200 GPUs, and TopoLVM storage class `lvms-vg1`. The two active model pods use `restricted-v2`. The shared SCC binding grants `nonroot-v2` to the Gateway service account only.

## Inspecting the Environment

Use these environment variables for deployment examples:

```bash
export NAMESPACE=llm-d
export MODEL_SERVER=vllm
export IGW_CHART_VERSION=v1.3.0
export MODEL_SERVICE_CHART_VERSION=v0.4.15
```

Before scaling or upgrading a model, inspect the relevant state:

```bash
oc get deployments,pods,pvc -n "${NAMESPACE}"
oc get inferencepool,httproute -n "${NAMESPACE}"
helm list -n "${NAMESPACE}"
oc describe nodes
```

Check GPU requests already allocated to pods. Node GPU capacity and allocatable labels alone do not indicate unused GPUs.

Discover the public hostname from the named Route:

```bash
export GATEWAY_URL=$(oc get route inference-gateway \
-n agentgateway-system -o jsonpath='{.spec.host}')
```

Do not use `apps.ocp4.redhat.com` or the first item from an unfiltered Route list as the endpoint. Use `curl -sS` so that connection and DNS errors remain visible. In shell automation, use `pipefail` when piping requests into `jq`.

## Selecting Images and Model Options

Before adding a model or changing an existing image, complete these checks in order:

1. Inspect the GPU model, driver, compute capability, and CUDA runtime. Distinguish the CUDA version reported by `nvidia-smi` from the CUDA runtime bundled with PyTorch or the image.
2. Read the exact checkpoint's [official vLLM recipe](https://recipes.vllm.ai/), including quantization, image, parser, template, and hardware requirements.
3. Verify that the published image tag exists and inspect its architecture and CUDA metadata. Do not invent tags or substitute a related model's recipe without evidence.
4. Review recipe options against the intended workload. Changes to KV-cache precision, templates, reasoning output, context length, or modalities require validation.
5. Render the chart and verify startup and inference with the selected checkpoint before reporting the image as working.

If the exact recipe is absent, document that gap and inspect the exact model's official documentation and versioned vLLM support. A generic recipe does not establish compatibility for another checkpoint.

Size model storage from the downloaded checkpoint files and required download overhead. Do not use GPU memory requirements as a substitute for disk-space requirements.

### Recorded Compatibility Review

The review on 28 October 2026 found driver `575.57.08` with CUDA support through `12.9`. The running Granite container uses PyTorch `2.10.0+cu129`. Registry inspection confirmed CUDA `12.9.1` in `vllm/vllm-openai:v0.32.0-cu129`; the default `v0.32.0` image contains CUDA `13.0.2`.

The [exact Gemma FP8-block recipe](https://recipes.vllm.ai/RedHatAI/gemma-4-31B-it-FP8-block.json) lists minimum vLLM `0.19.1` and recommends `latest`. The [official catalog](https://recipes.vllm.ai/models.json) has no Granite 4.1 30B FP8 entry at the time of review.

The recovery retains Gemma `:gemma4`, which reports `0.19.1.dev6+g6d4a8e6d2`, and Granite `:v0.19.0`. Both passed inference. The `:v0.32.0-cu129` image is an upgrade candidate; it has not been runtime-validated with these checkpoints. Recheck this dated evidence before an upgrade.

## Editing Model Manifests

Keep each model's PVC, download pod, values file, and model-specific instructions in `manifests/<model-key>/`. Use the active models as the reference for chart `v0.4.15`.

The two active model-service values files use these settings:

```yaml
modelArtifacts:
  readOnly: false
routing:
  servicePort: 8000
  proxy:
    enabled: false
decode:
  replicas: 1
  strategy:
    type: Recreate
  extraConfig:
    securityContext:
      fsGroupChangePolicy: OnRootMismatch
prefill:
  create: false
```

The vLLM container also sets `HOME=/tmp`, `HF_HOME=/tmp/huggingface`, and `mountModelVolume: true`. Container probe settings belong under `decode.containers[].extraConfig`.

Use `Recreate` for these RWO-backed model servers. Render `decode.strategy`, the PVC mount's read-only setting, the pod security context, container options, ports, and labels before applying a chart change. Do not assume that a value supported by `v0.4.15` works in `v0.4.9`.

The readiness probe checks HTTP `/health` on port `8000`, with `periodSeconds: 10` and `timeoutSeconds: 5`. Readiness failures do not restart containers. Do not introduce liveness or startup deadlines that kill normal model loading, compilation, or CUDA graph capture.

## Applying Deployment Changes

Follow the root README for complete commands. Use this sequence for a new model:

1. Verify GPU availability, image compatibility, storage capacity, and the required secret.
2. Create the PVC and run the download pod. Its init container downloads the checkpoint; its pause container becomes ready afterward.
3. Verify download completion and delete the download pod before starting the model server.
4. Install the model's InferencePool and EPP with chart `v1.3.0`. Match the pod `app` label and exact served model ID.
5. Set the generated HTTPRoute parent to Gateway `inference-gateway` in `agentgateway-system`. Recheck the reference after pool upgrades.
6. Add the exact model ID to `manifests/shared/agentgateway-policy.yaml` and apply the updated policy in `agentgateway-system`.
7. Install the model-service release with chart `v0.4.15` and its values file.
8. Verify both the model server and EPP, then send inference through the external Gateway.

Update an existing active model with its declared values:

```bash
helm upgrade --install ms-gemma-4-31b llm-d-modelservice/llm-d-modelservice \
--version "${MODEL_SERVICE_CHART_VERSION}" \
-f manifests/gemma-4-31b/values.yaml -n "${NAMESPACE}"
```

Starting both active models requires scaling both model-server deployments and both EPP deployments. Namespace-wide scaling also starts older installed models and can exceed GPU capacity. Record persistent scale changes in release configuration because a Helm upgrade can restore chart replica counts.

## Verifying Serving Changes

Require the following evidence before reporting deployment success:

- Ready model-server replicas and ready EPP replicas for every requested model
- Gateway conditions `Accepted=True` and `Programmed=True`
- HTTPRoute conditions `Accepted=True` and `ResolvedRefs=True`, with the correct Gateway parent
- Policy conditions `Accepted=True` and `Attached=True`
- Matching model pod labels, InferencePool selectors, model endpoint port `8000`, and picker service port `9002`
- HTTP 200 and a valid `choices` response from each requested model through the external Gateway

Inspect logs and events when startup is incomplete. A running pod or a Uvicorn log message alone does not prove that the Gateway can serve inference. Record tool calling, reasoning, or multimodal behavior as verified only when the corresponding requests pass.

The current body-routing policy does not provide a Gateway route for `GET /v1/models`. List configured models from HTTPRoutes, or query a running vLLM server directly on port `8000`. A policy mapping alone does not prove model readiness.

## Recovering Volume Mounts

The earlier migration encountered permission and SELinux setup errors on existing read-only mounts. Both models recovered with their original PVCs after complete scale-to-zero, release of the old mounts, and scale-up with writable model artifacts.

For this recovery, verify chart `v0.4.15` and the writable volume settings first. Stop the affected model server, wait for its pods and old mounts to disappear, and then start it again. Verify `rw` mount options, readiness, and inference through the Gateway.

Do not infer a CSI driver defect or a required SCC change from a read-only error alone. Do not change model-server SCC permissions without evidence that the requested change is necessary. Prefer a complete unmount and remount before recreating the PVC.

## Maintaining Repository Consistency

Keep the root README, model-specific instructions, and Helm values aligned when changing serving ports, chart versions, container images, model IDs, or volume settings. Update the root README after deployment verification so that it describes the final serving behavior.

Model IDs must agree across the download configuration, model-service values, InferencePool route configuration, shared policy mapping, and request examples. Preserve exact capitalization. Keep dated compatibility evidence separate from configuration requirements and identify image candidates that have not passed inference validation.
