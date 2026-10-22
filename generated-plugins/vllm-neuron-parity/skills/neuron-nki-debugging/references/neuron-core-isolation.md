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
# List devices, core IDs and the NeuronCore total from the plain neuron-ls table
# (the JSON flags --json-output and -j end in Neuron 2.34)
neuron-ls | python3 -c "
import sys
rows = [[c.strip() for c in l.strip()[1:-1].split('|')] for l in sys.stdin if l.strip().startswith('|')]
head = [' '.join(p).strip() for p in zip(*rows[:2])]
if 'NEURON DEVICE' not in head or 'NEURON CORES' not in head:
    sys.exit('neuron-ls: no device table with NEURON DEVICE and NEURON CORES columns')
dev, cores = head.index('NEURON DEVICE'), head.index('NEURON CORES')
ids = head.index('NEURON CORE IDS') if 'NEURON CORE IDS' in head else None
devs = [r for r in rows[2:] if r[dev].isdigit()]
if not devs:
    sys.exit('neuron-ls: device table has no device rows')
for r in devs:
    print('Device ' + r[dev] + ': ' + r[cores] + ' cores, IDs ' + (r[ids] if ids is not None else 'not shown'))
print('NeuronCores:', sum(int(r[cores]) for r in devs))
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
