"""Azure saved-plan boundaries; no production bypass or external process."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from cloud_network_benchmark import azure_provider as az
from cloud_network_benchmark import terraform_plan as tp
from cloud_network_benchmark.azure_plan_scope import security_rules
from tests.contract.test_azure_provider_contract import deployment, options
from tests.unit.test_terraform_plan_review import plan_document, SavedPlanRunner, operator, ROOT


def azure_scope(root="pair", scenario="same_zone", region="swedencentral"):
    v = dict(subscription_id="00000000-0000-0000-0000-000000000001",tenant_id="00000000-0000-0000-0000-000000000002",
        resource_group_name="scope",deployment_id="deploy",location=region)
    group = f'/subscriptions/{v["subscription_id"]}/resourceGroups/scope'
    arm = lambda kind,name: group+"/providers/"+kind+"/"+name
    tags = {"managed_by":"cloud-network-benchmark","deployment_id":"deploy"}
    if root == "expiry":
        r = {"azurerm_resource_group.attempt":dict(name="scope",location=region,tags={**tags,"purpose":"independent-expiry"}),
            "azurerm_automation_account.expiry":dict(name="scope-auto",location=region,resource_group_name="scope",sku_name="Basic",tags=tags,identity=[{"type":"SystemAssigned","principal_id":"principal"}]),
            "azurerm_role_definition.deallocate":dict(name="scope-deallocate-only",scope=group,assignable_scopes=[group],permissions=[{"actions":az.ROLE_ACTIONS}],role_definition_resource_id=arm("Microsoft.Authorization/roleDefinitions","role")),
            "azurerm_role_assignment.deallocate":dict(scope=group,principal_id="principal",principal_type="ServicePrincipal",role_definition_id=arm("Microsoft.Authorization/roleDefinitions","role"))}
        return v,r
    v.pop("tenant_id")
    v.update(scenario=scenario,region_a=region,region_b="uksouth" if scenario=="inter_region" else region,
        zone_a="1",zone_b="2" if scenario=="cross_zone" else "1",private_a="10.70.0.4",private_b="10.71.0.4" if scenario=="inter_region" else "10.70.0.5",
        create_ppg=scenario=="placement_optimization",create_peering=scenario=="inter_region",placement_group_name="scope-ppg" if scenario=="placement_optimization" else "",
        config_sha256="a"*64,controller_cidr="192.0.2.8/32",ssh_public_key="fixture-key",selected_image="Canonical:ubuntu-24_04-lts:server:24.04.202610250",active_priority="Regular",network_limit_mbps=12500,admin_username="azureuser")
    tags.update(config_sha256=v["config_sha256"],scenario=scenario)
    r = {}
    for key in (("a","b") if v["create_peering"] else ("a",)):
        common=dict(resource_group_name="scope",location=v["region_"+key])
        cidr="10.71.0.0" if key=="b" else "10.70.0.0"
        r[f'azurerm_virtual_network.pair["{key}"]']={**common,"name":f"scope-{key}-vnet","address_space":[cidr+"/16"],"tags":tags}
        r[f'azurerm_subnet.pair["{key}"]']=dict(name="benchmark",resource_group_name="scope",virtual_network_name=f"scope-{key}-vnet",address_prefixes=[cidr+"/24"])
    for role in ("a","b"):
        common=dict(resource_group_name="scope",location=v["region_"+role],tags=tags)
        r[f'azurerm_public_ip.pair["{role}"]']={**common,"name":f"scope-{role}-pip","allocation_method":"Static","sku":"Standard","zones":[v["zone_"+role]],"ip_version":"IPv4"}
        subnet_key=role if v["create_peering"] else "a"
        r[f'azurerm_network_interface.pair["{role}"]']={**common,"name":f"scope-{role}-nic","accelerated_networking_enabled":True,"ip_forwarding_enabled":False,"ip_configuration":[dict(name="private",private_ip_address_allocation="Static",private_ip_address=v["private_"+role],subnet_id=arm("Microsoft.Network/virtualNetworks",f"scope-{subnet_key}-vnet")+"/subnets/benchmark",public_ip_address_id=arm("Microsoft.Network/publicIPAddresses",f"scope-{role}-pip"))]}
        r[f'azurerm_network_security_group.pair["{role}"]']={**common,"name":f"scope-{role}-nsg","security_rule":security_rules(role,v)}
        r[f'azurerm_network_interface_security_group_association.pair["{role}"]']=dict(network_interface_id=arm("Microsoft.Network/networkInterfaces",f"scope-{role}-nic"),network_security_group_id=arm("Microsoft.Network/networkSecurityGroups",f"scope-{role}-nsg"))
        r[f'azurerm_linux_virtual_machine.pair["{role}"]']={**common,"name":f"scope-{role}","computer_name":role,"size":"Standard_D4s_v5","zone":v["zone_"+role],"priority":"Regular","admin_username":"azureuser","disable_password_authentication":True,"network_interface_ids":[arm("Microsoft.Network/networkInterfaces",f"scope-{role}-nic")],"os_disk":[dict(caching="ReadWrite",storage_account_type="StandardSSD_LRS",disk_size_gb=32)],"admin_ssh_key":[dict(username="azureuser",public_key=v["ssh_public_key"])],"source_image_reference":[dict(publisher="Canonical",offer="ubuntu-24_04-lts",sku="server",version="24.04.202610250")],"proximity_placement_group_id":arm("Microsoft.Compute/proximityPlacementGroups",v["placement_group_name"]) if v["create_ppg"] else None}
    if v["create_ppg"]: r["azurerm_proximity_placement_group.pair[0]"]=dict(name="scope-ppg",location=region,resource_group_name="scope",allowed_vm_sizes=["Standard_D4s_v5"],tags=tags)
    if v["create_peering"]:
        for role,other in (("a","b"),("b","a")):
            r[f"azurerm_virtual_network_peering.{role}_to_{other}[0]"]=dict(name=f"scope-{role}-to-{other}",resource_group_name="scope",virtual_network_name=f"scope-{role}-vnet",remote_virtual_network_id=arm("Microsoft.Network/virtualNetworks",f"scope-{other}-vnet"),allow_virtual_network_access=True,allow_forwarded_traffic=False,allow_gateway_transit=False,use_remote_gateways=False)
    return v,r


def azure_document(v,r):
    p=plan_document(v,r,"azure")
    for item in p["resource_changes"]: item["provider_name"]="registry.terraform.io/hashicorp/azurerm"
    p["configuration"]={"provider_config":{"azurerm":{"full_name":"registry.terraform.io/hashicorp/azurerm","expressions":{"subscription_id":{"references":["var.subscription_id"]},"resource_provider_registrations":{"constant_value":"none"}}}},"root_module":{"resources":[{"address":a,"provider_config_key":"azurerm","expressions":{}} for a in sorted({a.split("[",1)[0] for a in r})]}}
    return p


@pytest.mark.parametrize("root,scenario,region,count",[("expiry","same_zone","swedencentral",4),("pair","same_zone","swedencentral",12),("pair","same_zone","germanywestcentral",12),("pair","cross_zone","germanywestcentral",12),("pair","placement_optimization","germanywestcentral",13),("pair","inter_region","germanywestcentral",16)])
def test_native_scope_layouts(root,scenario,region,count):
    v,r=azure_scope(root,scenario,region)
    assert len(tp.validate_scope(azure_document(v,r),"azure",ROOT/"terraform/azure"/root,v))==count


@pytest.mark.parametrize("root",["expiry","pair"])
@pytest.mark.parametrize("mutation",["extra","missing","update","delete","replace","import","variables","alias","subscription"])
def test_scope_rejects_changed_actions_identity_and_provider(root,mutation):
    v,r=azure_scope(root);p=azure_document(v,r);item=p["resource_changes"][0]
    if mutation=="extra": p["resource_changes"].append({**deepcopy(item),"address":"azurerm_managed_disk.extra"})
    elif mutation=="missing": p["resource_changes"].pop()
    elif mutation in {"update","delete","replace"}: item["change"]["actions"]={"update":["update"],"delete":["delete"],"replace":["delete","create"]}[mutation]
    elif mutation=="import": item["change"]["importing"]={"id":"foreign"}
    elif mutation=="variables": p["variables"]["location"]["value"]="germanywestcentral"
    elif mutation=="alias": p["configuration"]["root_module"]["resources"][0]["provider_config_key"]="azurerm.foreign"
    else: p["configuration"]["provider_config"]["azurerm"]["expressions"]["subscription_id"]={"constant_value":"foreign"}
    with pytest.raises(tp.PlanReviewError): tp.validate_scope(p,"azure",ROOT/"terraform/azure"/root,v)


@pytest.mark.parametrize("root,address,path,value",[
 ("expiry","azurerm_automation_account.expiry",("location",),"germanywestcentral"),
 ("expiry","azurerm_automation_account.expiry",("sku_name",),"Other"),
 ("expiry","azurerm_role_definition.deallocate",("permissions",0,"actions"),["*"]),
 ("expiry","azurerm_role_definition.deallocate",("scope",),"/subscriptions/foreign"),
 ("expiry","azurerm_role_assignment.deallocate",("principal_id",),"foreign"),
 ("pair",'azurerm_linux_virtual_machine.pair["a"]',("size",),"Standard_D8s_v5"),
 ("pair",'azurerm_linux_virtual_machine.pair["a"]',("source_image_reference",0,"version"),"latest"),
 ("pair",'azurerm_linux_virtual_machine.pair["a"]',("os_disk",0,"disk_size_gb"),64),
 ("pair",'azurerm_linux_virtual_machine.pair["b"]',("network_interface_ids",),["foreign"]),
 ("pair",'azurerm_public_ip.pair["a"]',("ip_version",),"IPv6"),
 ("pair",'azurerm_network_interface.pair["b"]',("accelerated_networking_enabled",),False),
 ("pair",'azurerm_network_security_group.pair["b"]',("security_rule",0,"source_address_prefix"),"*"),
 ("pair",'azurerm_virtual_network.pair["a"]',("location",),"uksouth"),
])
def test_critical_scope_is_inspected(root,address,path,value):
    v,r=azure_scope(root);target=r[address]
    for k in path[:-1]: target=target[k]
    target[path[-1]]=value
    with pytest.raises(tp.PlanReviewError): tp.validate_scope(azure_document(v,r),"azure",ROOT/"terraform/azure"/root,v)


@pytest.mark.parametrize("foreign",[False,True])
def test_computed_iam_identity_requires_the_exact_plan_reference(foreign):
    v,r=azure_scope("expiry");r["azurerm_role_assignment.deallocate"].pop("principal_id")
    p=azure_document(v,r)
    config=next(x for x in p["configuration"]["root_module"]["resources"] if x["address"]=="azurerm_role_assignment.deallocate")
    config["expressions"]["principal_id"]={"references":["azurerm_automation_account."+("foreign" if foreign else "expiry")+".identity[0].principal_id"]}
    if foreign:
        with pytest.raises(tp.PlanReviewError): tp.validate_scope(p,"azure",ROOT/"terraform/azure/expiry",v)
    else: assert tp.validate_scope(p,"azure",ROOT/"terraform/azure/expiry",v)


@pytest.mark.parametrize("root",["expiry","pair"])
def test_azure_dispatch_applies_only_saved_binary_to_original_state(tmp_path,operator,root):
    d=deployment(ROOT);o=options(tmp_path);workspace,_=az._workspace(o,d)
    v,r=azure_scope(root);varfile=workspace/"vars.json";varfile.write_text(json.dumps(v))
    runner=SavedPlanRunner(azure_document(v,r))
    az._tf(workspace,o,runner,lambda:1.,f"terraform/azure/{root}","apply","-input=false",f"-var-file={varfile}",action=root+"_apply")
    assert [q.argv[2] for q in runner.requests]==["plan","show","apply"]
    applied=runner.requests[-1]
    assert f"-state={workspace/(root+'.tfstate')}" in applied.argv
    assert "-auto-approve" not in applied.argv and not any(a.startswith("-var-file") for a in applied.argv)
    binary=Path(applied.argv[-1]);assert binary.parent.parent==workspace and binary.exists()
    assert json.loads((binary.parent/"accepted-review.json").read_text())["binding"]["state"]==str(workspace/(root+".tfstate"))


@pytest.mark.parametrize("root",["expiry","pair"])
def test_missing_azure_receipt_preserves_evidence_and_still_allows_destroy(tmp_path,root):
    d=deployment(ROOT);o=options(tmp_path);workspace,_=az._workspace(o,d)
    v,r=azure_scope(root);varfile=workspace/"vars.json";varfile.write_text(json.dumps(v))
    runner=SavedPlanRunner(azure_document(v,r))
    with pytest.raises(tp.PlanReviewError,match="inspection required"):
        az._tf(workspace,o,runner,lambda:1.,f"terraform/azure/{root}","apply",f"-var-file={varfile}",action=root+"_apply")
    az._tf(workspace,o,runner,lambda:1.,f"terraform/azure/{root}","destroy","-auto-approve",f"-var-file={varfile}",action=root+"_destroy")
    assert [q.argv[2] for q in runner.requests]==["plan","show","destroy"]


@pytest.mark.parametrize("seconds",[0,30,600,-1,601,True,1.5,"30"])
def test_runtime_parser_accepts_only_bounded_integer_review_wait(tmp_path,seconds):
    from dataclasses import asdict, is_dataclass
    from tests.integration.test_f09_aws_runtime_factory import _runtime
    from cloud_network_benchmark.lifecycle_runtime import load_runtime_config
    source=tmp_path/"manifest.json";source.write_text("{}")
    runtime=_runtime(tmp_path,ROOT,source,source,"a"*64)
    runtime.pop("approval_evidence");runtime["schema_version"]=1
    runtime["allowance"]=asdict(runtime["allowance"])
    runtime["allowance"]["provider_leads_seconds"]={"azure":360}
    runtime["access"]["diagnostic_interfaces"]={"azure":{"vm_a":"eth0","vm_b":"eth0"}}
    row=runtime["providers"]["aws"]
    row["options"]={"subscription_id":"00000000-0000-0000-0000-000000000001","tenant_id":"00000000-0000-0000-0000-000000000002","controller_cidr":"192.0.2.8/32","selected_image":"Canonical:ubuntu-24_04-lts:server:24.04.202610250","ssh_public_key_path":row["options"]["public_key_path"],"authentication_reference":"synthetic","poll_seconds":15,"command_timeout_seconds":60,"plan_review_timeout_seconds":seconds}
    runtime["providers"]={"azure":row}
    def encode(x):
        if isinstance(x,Path): return str(x)
        if hasattr(x,"model_dump"): return x.model_dump(mode="json")
        if is_dataclass(x): return asdict(x)
        raise TypeError(type(x))
    path=tmp_path/"runtime.json";path.write_text(json.dumps(runtime,default=encode))
    if type(seconds) is int and 0<=seconds<=600:
        assert load_runtime_config(path,selected_providers={"azure"})["providers"]["azure"]["options"]["plan_review_timeout_seconds"]==seconds
    else:
        with pytest.raises(ValueError,match="integer in 0..600"): load_runtime_config(path)
    assert not (tmp_path/"ledger.json").exists()


def test_azure_financial_binding_includes_expiry_powershell(tmp_path):
    from types import SimpleNamespace
    from cloud_network_benchmark.lifecycle_runtime import _financial_runtime_binding
    from cloud_network_benchmark.contracts.common import StringEvidence
    root=tmp_path/"terraform/azure/expiry";root.mkdir(parents=True)
    script=root/"deallocate.ps1";script.write_text("fixture one")
    bootstrap=tmp_path/"bootstrap";bootstrap.write_text("fixture")
    child=SimpleNamespace(config_provenance=SimpleNamespace(implementation_git_commit=StringEvidence(value="a"*40)))
    runtime={"providers":{"azure":{"bootstrap_template":bootstrap,"terraform_directory":root.parent,"sources":{}}}}
    before=_financial_runtime_binding(runtime,{"azure":child})
    script.write_text("fixture two")
    assert _financial_runtime_binding(runtime,{"azure":child})!=before


def test_legacy_recovery_without_input_hash_or_admin_variable_retains_original_bytes(tmp_path):
    d=deployment(ROOT);o=options(tmp_path);workspace,journal=az._workspace(o,d)
    values=az.build_pair_inputs(d,o);values.pop("admin_username")
    path=az._persist_tfvars(workspace,journal,"pair",values)
    state=json.loads(journal.read_text());state.pop("terraform_input_sha256");az._atomic_private(journal,state)
    assert az._recovery_tfvars(workspace,journal,"pair",az.build_pair_inputs(d,o))==path
    assert "admin_username" not in json.loads(path.read_text())


@pytest.mark.parametrize("refs,valid",[
 (["azurerm_automation_account.expiry.identity[0].principal_id","azurerm_automation_account.expiry.identity","azurerm_automation_account.expiry"],True),
 (["azurerm_automation_account.expiry.identity[0].tenant_id","azurerm_automation_account.expiry"],False),
 (["azurerm_automation_account.expiry"],False),
])
def test_unknown_principal_requires_principal_attribute_not_just_resource(refs,valid):
    v,r=azure_scope("expiry");r["azurerm_role_assignment.deallocate"].pop("principal_id")
    p=azure_document(v,r)
    config=next(x for x in p["configuration"]["root_module"]["resources"] if x["address"]=="azurerm_role_assignment.deallocate")
    config["expressions"]["principal_id"]={"references":refs}
    if valid: assert tp.validate_scope(p,"azure",ROOT/"terraform/azure/expiry",v)
    else:
        with pytest.raises(tp.PlanReviewError): tp.validate_scope(p,"azure",ROOT/"terraform/azure/expiry",v)


@pytest.mark.parametrize("refs,valid",[
 (["azurerm_network_interface.pair","each.key"],True),
 (["azurerm_network_interface.pair"],False),
 (['azurerm_network_interface.pair["b"].id','azurerm_network_interface.pair["b"]','azurerm_network_interface.pair'],False),
])
def test_unknown_vm_nic_cannot_switch_to_literal_other_role(refs,valid):
    v,r=azure_scope();r['azurerm_linux_virtual_machine.pair["a"]']["network_interface_ids"]=[None]
    p=azure_document(v,r)
    config=next(x for x in p["configuration"]["root_module"]["resources"] if x["address"]=="azurerm_linux_virtual_machine.pair")
    config["expressions"]["network_interface_ids"]={"references":refs}
    if valid: assert tp.validate_scope(p,"azure",ROOT/"terraform/azure/pair",v)
    else:
        with pytest.raises(tp.PlanReviewError): tp.validate_scope(p,"azure",ROOT/"terraform/azure/pair",v)


def test_unknown_legacy_ppg_retains_exact_index_and_declared_condition():
    v,r=azure_scope("pair","placement_optimization","germanywestcentral")
    for role in ("a","b"):r[f'azurerm_linux_virtual_machine.pair["{role}"]']["proximity_placement_group_id"]=None
    p=azure_document(v,r)
    config=next(x for x in p["configuration"]["root_module"]["resources"] if x["address"]=="azurerm_linux_virtual_machine.pair")
    config["expressions"]["proximity_placement_group_id"]={"references":["var.create_ppg","azurerm_proximity_placement_group.pair[0].id","azurerm_proximity_placement_group.pair[0]","azurerm_proximity_placement_group.pair"]}
    assert tp.validate_scope(p,"azure",ROOT/"terraform/azure/pair",v)


@pytest.mark.parametrize("other,valid",[("b",True),("a",False)])
def test_unknown_legacy_peering_requires_the_opposite_network(other,valid):
    v,r=azure_scope("pair","inter_region","germanywestcentral")
    r['azurerm_virtual_network_peering.a_to_b[0]']["remote_virtual_network_id"]=None
    p=azure_document(v,r)
    config=next(x for x in p["configuration"]["root_module"]["resources"] if x["address"]=="azurerm_virtual_network_peering.a_to_b")
    config["expressions"]["remote_virtual_network_id"]={"references":[f'azurerm_virtual_network.pair["{other}"].id',f'azurerm_virtual_network.pair["{other}"]','azurerm_virtual_network.pair']}
    if valid: assert tp.validate_scope(p,"azure",ROOT/"terraform/azure/pair",v)
    else:
        with pytest.raises(tp.PlanReviewError): tp.validate_scope(p,"azure",ROOT/"terraform/azure/pair",v)


def test_unknown_vm_nic_list_still_requires_one_attachment():
    v,r=azure_scope();r['azurerm_linux_virtual_machine.pair["a"]']["network_interface_ids"]=[None,None]
    p=azure_document(v,r)
    config=next(x for x in p["configuration"]["root_module"]["resources"] if x["address"]=="azurerm_linux_virtual_machine.pair")
    config["expressions"]["network_interface_ids"]={"references":["azurerm_network_interface.pair","each.key"]}
    with pytest.raises(tp.PlanReviewError,match="cardinality"):
        tp.validate_scope(p,"azure",ROOT/"terraform/azure/pair",v)
