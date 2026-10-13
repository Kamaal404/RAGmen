#!/usr/bin/env bash
# Mutation suite for blueprint/scripts/validate_blueprint.py.
#
# Every case takes the reference blueprint, breaks exactly one thing, and
# asserts the validator rejects it with the expected message. A validator only
# ever run on good input has not been tested: the reference blueprint passing
# says nothing about whether a rule fires.
#
#   bash tests/test_blueprint.sh
#
# Run from the repo root; golden_set and vocabulary paths resolve against cwd.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VALIDATOR="blueprint/scripts/validate_blueprint.py"
REFERENCE="spec/examples/firecraft.blueprint.yaml"
PY="${PYTHON:-python}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0

ok()  { pass=$((pass + 1)); printf '  ok    %s\n' "$1"; }
bad() { fail=$((fail + 1)); printf '  FAIL  %s\n        %s\n' "$1" "$2"; }

# mutate NAME EXPECTED PYTHON
#   PYTHON is a statement run against `bp` (the parsed reference blueprint).
#   The mutated blueprint must exit 1 and print EXPECTED on an ERROR line.
mutate() {
    local name="$1" expected="$2" stmt="$3"
    local out="$TMP/$name.yaml"
    if ! "$PY" - "$REFERENCE" "$out" "$stmt" <<'EOF'
import sys, yaml
src, dst, stmt = sys.argv[1:4]
bp = yaml.safe_load(open(src, encoding="utf-8"))
exec(stmt)
yaml.safe_dump(bp, open(dst, "w", encoding="utf-8"), sort_keys=False, allow_unicode=True)
EOF
    then
        bad "$name" "mutation statement failed: $stmt"
        return
    fi
    run_expect_error "$name" "$expected" "$out"
}

# raw NAME EXPECTED CONTENT: for input that cannot be built by mutating YAML.
raw() {
    local name="$1" expected="$2" content="$3"
    local out="$TMP/$name.yaml"
    printf '%s' "$content" > "$out"
    run_expect_error "$name" "$expected" "$out"
}

# warns NAME EXPECTED PYTHON: the mutation must still validate (exit 0) but
# print EXPECTED on a warn line. For gaps that degrade silently, not crash.
warns() {
    local name="$1" expected="$2" stmt="$3"
    local out="$TMP/$name.yaml"
    "$PY" - "$REFERENCE" "$out" "$stmt" <<'EOF' || { bad "$name" "mutation statement failed"; return; }
import sys, yaml
src, dst, stmt = sys.argv[1:4]
bp = yaml.safe_load(open(src, encoding="utf-8"))
exec(stmt)
yaml.safe_dump(bp, open(dst, "w", encoding="utf-8"), sort_keys=False, allow_unicode=True)
EOF
    local output code
    output="$("$PY" "$VALIDATOR" "$out" 2>&1)"
    code=$?
    if [ "$code" -ne 0 ]; then
        bad "$name" "expected exit 0, got $code"
    elif ! grep -F -- "$expected" <<<"$output" | grep -q '^warn'; then
        bad "$name" "no warn line containing: $expected"
    else
        ok "$name"
    fi
}

run_expect_error() {
    local name="$1" expected="$2" file="$3"
    local output code
    output="$("$PY" "$VALIDATOR" "$file" 2>&1)"
    code=$?
    if [ "$code" -ne 1 ]; then
        bad "$name" "expected exit 1, got $code"
    elif ! grep -qF -- "$expected" <<<"$output"; then
        bad "$name" "exit 1 but no line containing: $expected"
    else
        ok "$name"
    fi
}

echo "baseline"
output="$("$PY" "$VALIDATOR" "$REFERENCE" 2>&1)"; code=$?
if [ "$code" -eq 0 ] && grep -qF "0 error(s)" <<<"$output"; then
    ok "reference blueprint validates clean"
else
    bad "reference blueprint validates clean" "exit $code: $(tail -3 <<<"$output")"
fi
"$PY" "$VALIDATOR" "$REFERENCE" --strict >/dev/null 2>&1; code=$?
if [ "$code" -eq 1 ]; then ok "--strict fails on warnings"; else bad "--strict fails on warnings" "exit $code"; fi

echo "input"
raw not_yaml "invalid YAML" $'name: [unclosed\n'
raw not_mapping "must be a mapping" $'- just\n- a list\n'

echo "spec"
mutate open_ontology       "spec: ontology/closed"        'bp["ontology"]["closed"] = False'
mutate missing_workload    "'workload' is a required"     'del bp["workload"]'
mutate unknown_top_key     "Additional properties"        'bp["extras"] = 1'
mutate bad_version         "spec: blueprint_version"      'bp["blueprint_version"] = "1.0"'
mutate lowercase_label     "spec: ontology/node_types/0/label" 'bp["ontology"]["node_types"][0]["label"] = "procedure"'
mutate unknown_source_type "spec: structure/source_types" 'bp["structure"]["source_types"] = ["rtf"]'

echo "structure"
mutate chunk_at_undeclared "chunk_at 'article' is not a declared" 'bp["structure"]["chunk_at"] = "article"'
mutate hierarchy_bad_regex "invalid regex"                 'bp["structure"]["hierarchy"][0]["pattern"] = "^(unclosed"'
mutate hierarchy_no_group  "pattern has no capture group"  'bp["structure"]["hierarchy"][1]["pattern"] = "^[A-Z]{3,}$"'
mutate example_mismatch    "does not match its own example" 'bp["structure"]["hierarchy"][1]["example"] = "lowercase heading"'
mutate xref_bad_regex      "xref_patterns[chapter_ref]: invalid regex" 'bp["structure"]["xref_patterns"][0]["pattern"] = "see (Chapter"'
mutate skip_bad_regex      "skip_regions: invalid regex"   'bp["structure"]["skip_regions"][0]["match"] = "^(CONTENTS"'

echo "ontology"
mutate pattern_bad_source  "source 'Recipe' is not a declared node type"  'bp["ontology"]["patterns"][0][0] = "Recipe"'
mutate pattern_bad_target  "target 'Recipe' is not a declared node type"  'bp["ontology"]["patterns"][0][2] = "Recipe"'
mutate pattern_bad_rel     "relation 'COOKS' is not a declared"           'bp["ontology"]["patterns"][0][1] = "COOKS"'
mutate rel_without_pattern "relationship 'MITIGATED_BY' has no pattern"   'bp["ontology"]["patterns"] = [p for p in bp["ontology"]["patterns"] if p[1] != "MITIGATED_BY"]'
mutate det_undeclared      "deterministic_relations: 'CITES'"             'bp["ontology"]["deterministic_relations"] = ["CITES"]'
mutate vocab_undeclared    "vocabularies: 'Spice'"                         'bp["ontology"]["vocabularies"]["Spice"] = {"source": "vocab/materials.csv"}'
mutate resolution_undecl   "resolution: 'Spice'"                           'bp["ontology"]["resolution"][0]["labels"].append("Spice")'
mutate temporal_undeclared "temporal.labels: 'Statute'"                    'bp["ontology"]["temporal"] = {"enabled": True, "labels": ["Statute"]}'

echo "embedding"
mutate dims_mismatch       "produces 384-d vectors but dimensions is 768" 'bp["embedding"]["dimensions"] = 768'
mutate target_undeclared   "embedding.targets[material_card]: 'Spice'"    'bp["embedding"]["targets"][2]["of"] = "Spice"'
mutate one_facet_multi     "fewer than two distinct facets"               'bp["embedding"]["targets"] = bp["embedding"]["targets"][:1]'

echo "workload"
mutate no_multi_hop        "no 'multi_hop' shape declared"  'bp["workload"]["shapes"] = [s for s in bp["workload"]["shapes"] if s["kind"] != "multi_hop"]'
mutate traverse_undeclared "must_traverse: 'COOKS'"         'bp["workload"]["shapes"][1]["must_traverse"].append("COOKS")'
mutate return_undeclared   "must_return: 'Recipe'"          'bp["workload"]["shapes"][3]["must_return"].append("Recipe")'

echo "guardrails"
mutate exclude_no_cypher   "mode 'exclude' needs a cypher clause" 'bp["guardrails"]["hard_filters"].append({"name": "x", "mode": "exclude"})'
mutate force_no_labels     "force_include needs labels"           'bp["guardrails"]["hard_filters"][0].pop("labels")'
mutate filter_undeclared   "hard_filters[hazards_always]: 'Recipe'" 'bp["guardrails"]["hard_filters"][0]["labels"].append("Recipe")'

echo "forge contract"
mutate no_loader           "Forge has no loader for 'docx'"         'bp["structure"]["source_types"].append("docx")'
mutate no_embedder         "Forge has no 'openai' embedder"         'bp["embedding"]["provider"] = "openai"'
mutate chunk_tpl_field     "chunk template uses {title}"            'bp["embedding"]["targets"][0]["text"] = "{path} {title}\n{text}"'
mutate entity_tpl_field    "entity template uses {description}"     'bp["embedding"]["targets"][1]["text"] = "{name}. {description}"'
mutate vocab_no_canonical  "has no 'canonical' column" '
import os
csv_path = os.path.join(os.path.dirname(dst), "bad_vocab.csv")
open(csv_path, "w", encoding="utf-8").write("name,aliases\nbirch bark,birch\n")
bp["ontology"]["vocabularies"]["Material"]["source"] = csv_path'
warns  vocab_missing_file  "does not exist. Resolution for this type passes through" 'bp["ontology"]["vocabularies"]["Material"]["source"] = "vocab/nope.csv"'
warns  unbuilt_tiers       "Forge does not implement tiers"          'bp["ontology"]["vocabularies"]["Material"]["tiers"] = ["exact", "embedding"]'
warns  xref_not_parsed     "Forge does not parse cross-references"   'pass'

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
