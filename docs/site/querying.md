# Querying

Scientific read endpoints live under `/api/v1/scientific/*`. Default
local deployments allow anonymous scientific reads.

If the database is empty, these endpoints can return empty `records`
arrays. Load [Demo Data](demo-data.md) first if you want guaranteed
non-empty examples.

## Curl Examples

```bash
# Species search by SMILES.
curl -G "http://127.0.0.1:8010/api/v1/scientific/species/search" \
    --data-urlencode "smiles=C"

# Thermo search by SMILES.
curl -G "http://127.0.0.1:8010/api/v1/scientific/thermo/search" \
    --data-urlencode "smiles=C"

# Kinetics search by reactants/products.
curl -G "http://127.0.0.1:8010/api/v1/scientific/kinetics/search" \
    --data-urlencode "reactants=[CH3]" \
    --data-urlencode "reactants=[H][H]" \
    --data-urlencode "products=C" \
    --data-urlencode "products=[H]"
```

Always URL-encode bracketed SMILES with `--data-urlencode`; otherwise
shells and curl can interpret brackets as URL ranges.

## Python Example

Install the local Python client once:

```bash
pip install -e ./clients/python
```

Then run the guided read demo:

```bash
python clients/python/examples/scientific_reads.py \
    --base-url http://127.0.0.1:8010/api/v1 \
    --smiles "C" \
    --reactant "[CH3]" --reactant "[H][H]" \
    --product "C" --product "[H]"
```

The example prints primary public refs such as species refs,
reaction-entry refs, kinetics refs, thermo refs, geometry refs, and
level-of-theory refs. Use those refs for follow-up reads.

## More Query Guides

- [Scientific query cookbook](../guides/scientific_query_cookbook.md)
- [Public hosted querying](../guides/public_hosted_querying.md)
- [Workflow tool scientific reads](../guides/workflow_tool_scientific_reads.md)
