#!/usr/bin/env python3
"""
Patch vLLM's Gemma 4 weight loader for flattened MoE checkpoints.

Based on: https://gist.github.com/lioreshai/07b2ccb2c69616504d01b25383dfe895

Fixes:
1. Missing .moe. prefix in flattened checkpoints (RedHatAI, etc.)
2. Expert scale suffix handling (.weight_scale, etc.)
"""

import pathlib
import sys

def patch_gemma4(input_file, output_file):
    """Apply RedHatAI flattened MoE patch to gemma4.py"""

    GEMMA4_PY = pathlib.Path(input_file)
    GEMMA4_PATCHED = pathlib.Path(output_file)

    src = GEMMA4_PY.read_text()

    if "# PATCH: flattened MoE" in src:
        print("[patch] gemma4.py: already patched")
        GEMMA4_PATCHED.write_text(src)
        return 0

    count = 0

    # Patch 1: Expert mapping with suffix + .moe. handling
    OLD_EXPERT = "\n".join([
        "                    if weight_name not in name:",
        "                        continue",
        "                    moe_name = name.replace(weight_name, param_name)",
        "                    if moe_name not in params_dict:",
        "                        continue",
    ])

    NEW_EXPERT = "\n".join([
        "                    # PATCH: flattened MoE — proper suffix + .moe. handling",
        "                    _idx = name.rfind(weight_name)",
        "                    if _idx == -1:",
        "                        continue",
        "                    if _idx > 0 and name[_idx - 1] != '.':",
        "                        continue",
        "                    _prefix = name[:_idx]",
        "                    _expert_part = name[_idx:]",
        "                    if _expert_part == weight_name:",
        "                        _suffix = 'weight'",
        "                    elif _expert_part.startswith(weight_name + '.'):",
        "                        _suffix = _expert_part[len(weight_name) + 1:]",
        "                    else:",
        "                        continue",
        "                    _base = param_name.rsplit('weight', 1)[0] if 'weight' in param_name else param_name",
        "                    moe_name = f'{_prefix}{_base}{_suffix}'",
        "                    if moe_name not in params_dict:",
        "                        if '.experts.' in moe_name and '.moe.experts.' not in moe_name:",
        "                            moe_name = moe_name.replace('.experts.', '.moe.experts.')",
        "                    if moe_name not in params_dict:",
        "                        continue",
    ])

    if OLD_EXPERT in src:
        src = src.replace(OLD_EXPERT, NEW_EXPERT, 1)
        count += 1
        print("[patch] applied: expert mapping with suffix + .moe. fix")
    else:
        print("[patch] SKIP: expert mapping target not found", file=sys.stderr)

    # Patch 2: Fix weight_loader call
    OLD_LOADER = '                        weight_name + ".weight",'
    NEW_LOADER = '                        moe_name,  # PATCH: use mapped name'

    if OLD_LOADER in src:
        src = src.replace(OLD_LOADER, NEW_LOADER, 1)
        count += 1
        print("[patch] applied: weight_loader uses moe_name")
    else:
        print("[patch] SKIP: weight_loader target not found", file=sys.stderr)

    # Patch 3: Fallback for router/non-expert MoE weights
    OLD_FALLBACK = "\n".join([
        "                else:",
        '                    if name.endswith(".bias") and name not in params_dict:',
    ])
    NEW_FALLBACK = "\n".join([
        "                else:",
        "                    # PATCH: flattened MoE — insert .moe. for router/scale",
        "                    if name not in params_dict:",
        "                        if '.experts.' in name and '.moe.experts.' not in name:",
        "                            name = name.replace('.experts.', '.moe.experts.')",
        "                        elif '.router.' in name and '.moe.router.' not in name:",
        "                            name = name.replace('.router.', '.moe.router.')",
        '                    if name.endswith(".bias") and name not in params_dict:',
    ])

    if OLD_FALLBACK in src:
        src = src.replace(OLD_FALLBACK, NEW_FALLBACK, 1)
        count += 1
        print("[patch] applied: fallback router/scale .moe. fix")
    else:
        print("[patch] SKIP: fallback target not found", file=sys.stderr)

    if count > 0:
        GEMMA4_PATCHED.write_text(src)
        print(f"[patch] gemma4.py: {count} patch(es) applied")
        return 0
    else:
        print("[patch] gemma4.py: no patches applied", file=sys.stderr)
        return 1

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input_gemma4.py> <output_gemma4_patched.py>")
        sys.exit(1)

    sys.exit(patch_gemma4(sys.argv[1], sys.argv[2]))
