# Neuron Core Isolation for Concurrent Agents

Multiple agents may compile and run NKI kernels concurrently (e.g., optimizer running profiling while debugger tests a fix). Each agent must use isolated neuron cores to prevent device contention.

## NEURON_RT_VISIBLE_CORES

Pin each agent to the neuron cores its job lease names, using `NEURON_RT_VISIBLE_CORES`:

```bash
# The cores come from the job lease (the hardware queue tool's grant), never a fixed index
export NEURON_RT_VISIBLE_CORES="$LEASED_CORES"
```

| Environment Variable | Purpose |
|---------------------|---------|
| `NEURON_RT_VISIBLE_CORES` | Comma-separated list of core indices visible to this process |

## Allocation Strategy

When multiple agents run concurrently, the lease partitions the cores: each job asks
the hardware queue tool for the cores it needs and uses only the ones its grant names.
No agent picks a core index by convention, because two agents that both pick "0"
contend on one core.

**Single-core default:** For debugging and testing, `--lnc 1` (single NeuronCore) is already standard. Combined with `NEURON_RT_VISIBLE_CORES`, this ensures no contention:

```python
os.environ["NEURON_CC_FLAGS"] = "--target trn2 --lnc 1"
os.environ["NEURON_RT_VISIBLE_CORES"] = LEASED_CORES  # the cores the lease names
```

## Detecting Available Cores

```bash
# Count available NeuronCores
neuron-ls | grep -c "NeuronCore"

# List core details
neuron-ls --json-output | python3 -c "
import json, sys
data = json.load(sys.stdin)
for dev in data:
    for nc in dev.get('neuron_cores', []):
        print(f\"Core {nc['id']}: {nc['status']}\")
"
```

## Defensive Check Before Kernel Execution

```bash
# Check if target core is in use
neuron-top -n 1 2>/dev/null | grep "NeuronCore $CORE_ID" || true
```

## File Output Isolation

Each concurrent agent should also use unique output directories to prevent NEFF/NTFF collisions:

```python
import os
session_id = os.getpid()
os.environ['NEURON_RT_INSPECT_OUTPUT_DIR'] = f'./output/session-{session_id}'
```
