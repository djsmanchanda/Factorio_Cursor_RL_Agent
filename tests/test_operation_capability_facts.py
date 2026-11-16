# Path: tests/test_operation_capability_facts.py | Purpose: Keep native capability and owned-source diagnostics correlated without changing recovery decisions.
import json
from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as ab, live_base, operation_facts
from orchestrator.operation_invocations import Invocations
from orchestrator.operation_session import DeterministicSession
from planners.steel_bootstrap import steel_seed

pytestmark = [pytest.mark.deterministic, pytest.mark.fast]


class NativeClient:
    """Execute the actual /sc query with strict 2.1 inventory names and mining_area."""
    def __init__(self, *, steel_recipe=True):
        lupa = pytest.importorskip("lupa")
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.sent, self.replies = [], []

        def decode(value):
            if not hasattr(value, "items"):
                return value
            parts = dict(value.items())
            if parts and set(parts) == set(range(1, len(parts) + 1)):
                return [decode(parts[i]) for i in range(1, len(parts) + 1)]
            return {k: decode(v) for k, v in parts.items()}

        self.lua.globals().encode = lambda obj: json.dumps(decode(obj))
        self.lua.globals().parse = lambda raw: self.lua.table_from(json.loads(raw), recursive=True)
        self.lua.globals().emit = self.replies.append
        self.lua.execute("""
            defines={entity_status={working=1,no_ingredients=2,no_minable_resources=3},
                     inventory={crafter_input=1,crafter_output=2,chest=3}}
            helpers={table_to_json=encode,json_to_table=parse};rcon={print=emit}
            local force={name='player',index=1}
            entities={};resources={
                {name='iron-ore',position={x=22.5,y=4.5},amount=6851}}
            function add(name,kind,x,y,recipe,crafts,status)
                local e={name=name,type=kind,position={x=x,y=y},force=force,
                    unit_number=#entities+5251,active=true,energy=3306.67,
                    direction=4,status=status,products_finished=crafts}
                e.get_recipe=function() return recipe and {name=recipe,ingredients={}} end
                e.get_inventory=function(index)
                    assert(index==defines.inventory.crafter_input or
                        index==defines.inventory.crafter_output or index==defines.inventory.chest)
                    return {get_contents=function() return {{name='iron-plate',count=2}} end,
                            get_item_count=function() return 2 end}
                end
                entities[#entities+1]=e;return e
            end
            drill=add('electric-mining-drill','mining-drill',18.5,4.5,nil,0,3)
            drill.mining_area={left_top={x=16,y=2},right_bottom={x=21,y=7}}
            drill.drop_position={x=20.5,y=4.5}
            iron=add('electric-furnace','furnace',21.5,4.5,'iron-plate',3368,2)
            steel=add('electric-furnace','furnace',25.5,4.5,'steel-plate',740,2)
            local s={name='nauvis',index=1}
            s.find_entities_filtered=function(filter)
                local out={}
                if filter.type=='entity-ghost' then return out end
                if filter.type=='resource' then
                    for _,r in ipairs(resources) do
                        local a=filter.area
                        if r.position.x>=a.left_top.x and r.position.x<a.right_bottom.x and
                           r.position.y>=a.left_top.y and r.position.y<a.right_bottom.y then
                            out[#out+1]=r end
                    end
                    return out
                end
                for _,e in ipairs(entities) do
                    if e.name==filter.name and (not filter.force or e.force==filter.force) and
                       (not filter.position or (e.position.x==filter.position.x and
                                                e.position.y==filter.position.y)) then
                        out[#out+1]=e end
                end
                return out
            end
            game={tick=659414,surfaces={nauvis=s},forces={player=force}}
        """)
        if not steel_recipe:
            self.lua.execute("steel.get_recipe=function() return nil end")

    def command(self, text):
        self.sent.append(text)
        self.lua.execute(text.removeprefix('/sc '))
        return str(self.replies[-1])


def session(tmp_path, client, action):
    s = DeterministicSession.__new__(DeterministicSession)
    s.invocations = Invocations(tmp_path, "episode")
    s.client = client
    s.capture_capability_facts = True
    s.bridge = SimpleNamespace(build_layout=lambda *_: pytest.fail("telemetry must not build"))
    s._operations = lambda: {"produce": action}
    s._maintain_mall = lambda: None
    return s


@pytest.mark.parametrize("recipe,expected,basis", [
    (True, None, "historical_non_tier_crafts"),
    (False, "steel-plate", "no_matching_line"),
])
def test_exact_native_reply_explains_ladder_choice_without_changing_it(tmp_path, monkeypatch, recipe, expected, basis):
    client = NativeClient(steel_recipe=recipe)
    monkeypatch.setitem(ab.LINE_RECIPES, "steel-plate", {"machine": "electric-furnace"})
    monkeypatch.setattr(ab, "_chemical_ladder_predecessors", lambda _item: ("steel-plate",))
    selected = []

    def choose(**_params):
        selected.append(ab._missing_chemical_ladder_predecessor(client, "nauvis", "player", "plastic-bar"))

    s = session(tmp_path, client, choose)
    result = s.run("produce", item="plastic-bar")
    record = json.loads((s.invocations.directory / (result["operation_id"] + ".json")).read_text())
    facts = record["facts"]
    assert selected == [expected]
    assert [f["kind"] for f in facts] == [
        "line_query", "production_capability", "chemical_capability", "chemical_ladder_selection"]
    raw, metadata = facts[0]["raw_reply"].split("|@")
    assert raw.startswith("1 0 25.5:4.5 740" if recipe else "0 0")
    assert json.loads(metadata) == {"tick": 659414, "surface": "nauvis", "surface_index": 1,
                                    "force": "player", "force_index": 1}
    assert facts[0]["query"] == client.sent[0][4:]
    assert facts[1]["basis"] == basis
    assert facts[0]["correlation"] == {"state": "validated", **json.loads(metadata)}
    assert facts[1]["query_evidence"] == facts[2]["query_evidence"] == {
        "query_id": facts[0]["query_id"], "correlation": facts[0]["correlation"]}
    assert facts[2]["result"] is (expected is None)
    assert facts[3]["selected"] == expected
    assert record["finished"] and not record["submissions"]
    Invocations(tmp_path, "episode").require_clear()  # Facts do not become dispatch authority.
    assert not operation_facts.enabled()
    before = (s.invocations.directory / (result["operation_id"] + ".json")).read_bytes()
    assert ab._production_started(client, "nauvis", "player", "steel-plate") is recipe
    assert (s.invocations.directory / (result["operation_id"] + ".json")).read_bytes() == before


def test_exact_seed_health_retains_complete_native_mining_area_and_missing_entities():
    client = NativeClient()
    plan = steel_seed((18.5, 4.5), "east", mined=True)
    result = live_base.plan_entity_health(client, "nauvis", "player", plan)
    assert result["tick"] == 659414 and len(result["entities"]) == 8
    drill = result["entities"][0]
    assert drill["status"] == "no_minable_resources"
    assert drill["mining_area"] == {"left_top": {"x": 16, "y": 2}, "right_bottom": {"x": 21, "y": 7}}
    assert drill["resources_in_mining_area"] == {}
    iron, steel = result["entities"][1], result["entities"][4]
    assert (iron["lifetime_crafts"], steel["lifetime_crafts"]) == (3368, 740)
    assert steel["input"] == [{"name": "iron-plate", "count": 2}]
    assert not result["entities"][6]["found"]
    assert result["entities"][6]["errors"]["entity"] == "missing exact live identity"


def test_mining_census_includes_all_resource_names_in_the_native_area():
    client = NativeClient()
    client.lua.execute("""
        drill.status=defines.entity_status.working
        resources[#resources+1]={name='iron-ore',position={x=18.5,y=2.5},amount=5}
        resources[#resources+1]={name='copper-ore',position={x=18.5,y=3.5},amount=9}
    """)
    result = live_base.plan_entity_health(client, "nauvis", "player", steel_seed((18.5, 4.5), "east", mined=True))
    drill = result["entities"][0]
    assert [(r["name"], r["amount"]) for r in drill["resources_in_mining_area"]] == [
        ("iron-ore", 5), ("copper-ore", 9)]


def test_native_belt_health_reads_both_lanes_and_retains_errors():
    client = NativeClient()
    client.lua.execute("""
        belt=add('transport-belt','transport-belt',5.5,10.5,nil,0,1)
        belt.get_transport_line=function(i) return {get_contents=function()
            return {{name=i==1 and 'iron-plate' or 'copper-plate',count=i+1}} end} end
    """)
    plan = {"phases": [{"name": "source", "actions": [{"action_type": "place_ghost",
        "entity": "transport-belt", "position": {"x": 5.5, "y": 10.5}}]}]}
    row = live_base.plan_entity_health(client, "nauvis", "player", plan)["entities"][0]
    assert row["reads"]["belt_contents"] == "value"
    assert row["belt_contents"] == [{"name": "iron-plate", "count": 2}, {"name": "copper-plate", "count": 3}]
    client.lua.execute("belt.get_transport_line=function(i) error('unsupported') end")
    row = live_base.plan_entity_health(client, "nauvis", "player", plan)["entities"][0]
    assert row["reads"]["belt_contents"] == "error" and "belt_contents" not in row


@pytest.mark.parametrize("diagnostic_error", [False, True])
def test_saved_seed_retry_preserves_plan_and_original_outcome_with_health_facts(tmp_path, monkeypatch, diagnostic_error):
    client = NativeClient()
    plan = steel_seed((18.5, 4.5), "east", mined=True)
    path = tmp_path / "materials.steel-seed.json"
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(ab, "_MATERIAL_RESERVATION_LEDGER", SimpleNamespace(path=tmp_path / "materials.json"))
    monkeypatch.setattr(ab, "_bootstrap_state", lambda *_: SimpleNamespace(lifecycle_state="released"))
    monkeypatch.setattr(ab, "_adopt_legacy_steel_seed_reservation", lambda *_: None)
    monkeypatch.setattr(ab, "_transferable_or_available_stock", lambda *_: {})
    # Keep this telemetry-only contract independent of the new source remedy.
    monkeypatch.setattr(ab, "_recover_steel_seed_source", lambda *_: None)
    monkeypatch.setattr(ab, "bring_stage_up", lambda *_args, **_kw: None)
    monkeypatch.setattr(ab, "_ensure_power_anchor_on_generated_network", lambda *_: None)
    monkeypatch.setattr(ab, "_diagnose_machines", lambda *_args, **_kw: "no ingredients")
    calls = []
    monkeypatch.setattr(ab, "_submit", lambda _c, _b, _s, p, *_args, **_kw: calls.append(p))
    if diagnostic_error:
        monkeypatch.setattr(live_base, "plan_entity_health", lambda *_: (_ for _ in ()).throw(ValueError("unreadable")))
    facts = []
    before = path.read_bytes()
    with operation_facts.capture(facts.append), pytest.raises(ab.ProductionPrerequisiteDeferred) as error:
        ab._build_compact_steel_seed(client, object(), "nauvis", "player", (28.5, 4.5), (0, 0), lambda *_: None)
    assert error.value.code == "steel_seed_output_wait"
    assert calls == [plan] and path.read_bytes() == before
    health = [f for f in facts if f["kind"] == "owned_steel_seed_health"]
    assert [f["phase"] for f in health] == ["before_submit", "after_diagnosis"]
    assert all(f["reused"] and f["plan_sha256"] == health[0]["plan_sha256"] for f in health)
    if diagnostic_error:
        assert all(f["unknown"] == "ValueError: unreadable" and "observation" not in f for f in health)
    else:
        assert all(f["observation"]["entities"][0]["status"] == "no_minable_resources" for f in health)


def test_interrupted_operation_retains_facts_but_cannot_leak_into_maintenance(tmp_path):
    def action(**_params):
        operation_facts.record("selected_read", value=7)
        raise RuntimeError("interrupted before return")
    s = session(tmp_path, object(), action)
    with pytest.raises(RuntimeError, match="interrupted"):
        s.run("produce", item="steel-plate")
    record = json.loads(next(s.invocations.directory.glob("*.json")).read_text())
    assert not record["finished"] and record["facts"] == [{"kind": "selected_read", "value": 7}]
    assert not operation_facts.enabled()
    operation_facts.record("outside")
    assert json.loads(next(s.invocations.directory.glob("*.json")).read_text()) == record


@pytest.mark.parametrize("enabled", [False, True])
def test_optional_capture_stays_outside_maintenance_and_preserves_result(tmp_path, enabled):
    def action(**_params):
        operation_facts.record("selected")
        return (1, 2)
    s = session(tmp_path, object(), action)
    s.capture_capability_facts = enabled
    s._maintain_mall = lambda: operation_facts.record("maintenance")
    result = s.run("produce", item="steel-plate")
    record = json.loads(next(s.invocations.directory.glob("*.json")).read_text())
    assert result["code"] == "producing"
    assert record.get("facts", []) == ([{"kind": "selected"}] if enabled else [])
    assert not operation_facts.enabled()


@pytest.mark.parametrize("failure", ["Cannot execute command", None])
def test_failed_line_read_retains_error_or_raw_reply_and_never_fabricates_capability(tmp_path, monkeypatch, failure):
    class Client:
        def command(self, _text):
            if failure is None:
                raise TimeoutError("no reply")
            return failure
    client = Client()
    monkeypatch.setitem(ab.LINE_RECIPES, "steel-plate", {"machine": "electric-furnace"})
    s = session(tmp_path, client, lambda **_: ab._production_started(client, "nauvis", "player", "steel-plate"))
    with pytest.raises((TimeoutError, ValueError)):
        s.run("produce", item="steel-plate")
    record = json.loads(next(s.invocations.directory.glob("*.json")).read_text())
    assert not record["finished"]
    assert len(record["facts"]) == 1
    fact = record["facts"][0]
    if failure is None:
        assert fact["kind"] == "line_query_error" and fact["error"] == "TimeoutError: no reply"
    else:
        assert fact["kind"] == "line_query" and fact["raw_reply"] == failure


@pytest.mark.parametrize("suffix", [
    "", "|@not-json",
    '|@{"tick":-10,"surface":"other","surface_index":0,"force":"enemy","force_index":0}',
    '|@{"tick":true,"surface":"nauvis","surface_index":1,"force":"player","force_index":1}',
    '|@{"tick":50,"tick":51,"surface":"nauvis","surface_index":1,"force":"player","force_index":1}',
    '|@{"tick":50,"surface":"nauvis","surface_index":true,"force":"player","force_index":1}',
    '|@{"tick":50,"surface":"nauvis","surface_index":1,"force":"player","force_index":1,"state":"validated"}',
])
def test_invalid_line_metadata_is_unknown_without_changing_controller_boolean(monkeypatch, suffix):
    class Client:
        def command(self, _text):
            return "1 0 25.5:4.5 740|1|0||0" + suffix
    facts = []
    monkeypatch.setitem(ab.LINE_RECIPES, "steel-plate", {"machine": "electric-furnace"})
    with operation_facts.capture(facts.append):
        assert ab._chemical_capability_started(Client(), "nauvis", "player", "steel-plate")
    assert facts[0]["correlation"]["state"] == "unknown"
    for fact in facts[1:]:
        assert fact["result"] is True
        assert fact["query_evidence"]["query_id"] == facts[0]["query_id"]
        assert fact["query_evidence"]["correlation"]["state"] == "unknown"


def test_nil_health_reads_are_explicit_absence_while_false_is_a_value():
    client = NativeClient()
    client.lua.execute("""
        drill.mining_target=nil;drill.electric_network_id=nil
        steel.get_recipe=function() return nil end
        steel.get_inventory=function() return nil end;steel.active=false
    """)
    result = live_base.plan_entity_health(client, "nauvis", "player", steel_seed((18.5, 4.5), "east", mined=True))
    drill, steel = result["entities"][0], result["entities"][4]
    assert all(drill["reads"][key] == "absent" for key in ("mining_target", "electric_network_id"))
    assert all(steel["reads"][key] == "absent" for key in ("recipe", "input", "output"))
    assert steel["reads"]["active"] == "value" and steel["active"] is False


@pytest.mark.parametrize("missing", ["all", "mining_area"])
def test_health_reply_cannot_omit_read_coverage(missing):
    client = NativeClient()
    plan = steel_seed((18.5, 4.5), "east", mined=True)
    result = live_base.plan_entity_health(client, "nauvis", "player", plan)
    for row in result["entities"]:
        if row["found"]:
            if missing == "all":
                row.pop("reads", None)
            elif row["expected"]["name"] == "electric-mining-drill":
                row.pop("mining_area", None)
                row.get("reads", {}).pop("mining_area", None)
    reply = json.dumps(result)
    client.command = lambda _text: reply
    with pytest.raises(ValueError, match="health read"):
        live_base.plan_entity_health(client, "nauvis", "player", plan)


@pytest.mark.parametrize("contradiction", ["value_missing", "absent_present", "error_without_error"])
def test_health_reply_rejects_contradictory_read_states(contradiction):
    client = NativeClient()
    plan = steel_seed((18.5, 4.5), "east", mined=True)
    result = live_base.plan_entity_health(client, "nauvis", "player", plan)
    row = result["entities"][4]
    if contradiction == "value_missing":
        row.pop("input")
    elif contradiction == "absent_present":
        row["reads"]["input"] = "absent"
    else:
        row.pop("input")
        row["reads"]["input"] = "error"
    client.command = lambda _text: json.dumps(result)
    with pytest.raises(ValueError, match="health read state"):
        live_base.plan_entity_health(client, "nauvis", "player", plan)
