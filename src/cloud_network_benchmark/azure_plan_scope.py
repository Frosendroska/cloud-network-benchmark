"""Exact Azure expiry/pair scope checks for private saved-plan inspection."""
from __future__ import annotations

from .terraform_plan import PlanReviewError, _fields, _same


def addresses(root: str, v: dict) -> set[str]:
    if root == "expiry":
        return {"azurerm_resource_group.attempt", "azurerm_automation_account.expiry",
                "azurerm_role_definition.deallocate", "azurerm_role_assignment.deallocate"}
    if root != "pair": raise PlanReviewError("unrecognized Azure root")
    result = {f'azurerm_{kind}.pair["{role}"]' for role in ("a", "b") for kind in
        ("public_ip", "network_security_group", "network_interface",
         "network_interface_security_group_association", "linux_virtual_machine")}
    for key in (("a", "b") if v["create_peering"] else ("a",)):
        result |= {f'azurerm_virtual_network.pair["{key}"]', f'azurerm_subnet.pair["{key}"]'}
    if v["create_ppg"]: result.add("azurerm_proximity_placement_group.pair[0]")
    if v["create_peering"]: result |= {"azurerm_virtual_network_peering.a_to_b[0]", "azurerm_virtual_network_peering.b_to_a[0]"}
    return result


def _one(value, label):
    if not isinstance(value, list) or len(value) != 1: raise PlanReviewError("expected one Azure " + label)
    return value[0]


def _refs(node, field):
    if not isinstance(node, (dict, list)): return []
    if isinstance(node, dict):
        if field in node and isinstance(node[field], dict) and "references" in node[field]:
            return node[field]["references"]
        children = node.values()
    else: children = node
    return [ref for child in children for ref in _refs(child, field)]


def _link(actual, expected, config, field, target, *, dynamic=False, condition=None):
    """Prove known IDs or the exact static attribute/dynamic per-role reference."""
    if actual is not None and expected is not None and not (isinstance(actual,list) and any(x is None for x in actual)):
        normalize = lambda x: [s.lower() for s in x] if isinstance(x, list) else x.lower()
        _same(normalize(actual), normalize(expected), "Azure " + field)
        return
    if isinstance(actual,list) and isinstance(expected,list):
        _same(len(actual),len(expected),"Azure " + field + " cardinality")
    refs = _refs(config, field)
    if dynamic:
        # Terraform truncates a traversal at a dynamic index. Require the owned
        # collection plus the role loop; a literal other-role index is forbidden.
        allowed = {target, "each.key", "each.value", "each.value.vnet_key"}
        proved = target in refs and any(x in refs for x in ("each.key", "each.value.vnet_key"))
    else:
        # Static references must reach the actual ID attribute, not merely the
        # same resource (e.g. tenant_id instead of principal_id).
        parts = target.split(".")
        allowed = {".".join(parts[:i]) for i in range(2,len(parts)+1)}
        allowed |= {ref.split("[",1)[0] for ref in list(allowed)}
        proved = target in refs
    if condition is not None: allowed.add(condition)
    if not proved or not refs or set(refs) - allowed:
        raise PlanReviewError("unproved or foreign computed Azure " + field)


def security_rules(role: str, v: dict) -> list[dict]:
    """The two explicit SSH rules, B's listeners, then default VNet denial."""
    def rule(name, priority, access, protocol, port, source):
        return dict(name=name, priority=priority, access=access, direction="Inbound", protocol=protocol,
                    source_port_range="*", destination_port_range=port, source_address_prefix=source,
                    destination_address_prefix="*")
    rules = [rule("allow-controller-ssh",100,"Allow","Tcp","22",v["controller_cidr"]),
             rule("deny-other-ssh",110,"Deny","Tcp","22","*")]
    if role == "b":
        for i, (name, protocol, port) in enumerate((("sockperf","Udp","11111"),("iperf3","Tcp","5201"),("icmp","Icmp","*"))):
            rules += [rule("allow-a-"+name,120+i*10,"Allow",protocol,port,v["private_a"]),
                      rule("deny-other-"+name,160+i*10,"Deny",protocol,port,"*")]
    return rules + [rule("deny-default-vnet-source",4000,"Deny","*","*","VirtualNetwork")]


def validate(r: dict, root: str, v: dict, plan: dict) -> None:
    configuration = plan.get("configuration", {})
    providers = configuration.get("provider_config", {})
    provider = providers.get("azurerm", {})
    _same(provider.get("full_name"), "registry.terraform.io/hashicorp/azurerm", "Azure provider configuration")
    _same(provider.get("expressions", {}).get("subscription_id"), {"references":["var.subscription_id"]}, "Azure subscription binding")
    _same(provider.get("expressions", {}).get("resource_provider_registrations"), {"constant_value":"none"}, "Azure automatic provider registration suppression")
    if provider.get("expressions", {}).get("resource_providers_to_register", {"constant_value":[]}) != {"constant_value":[]}:
        raise PlanReviewError("Azure automatic provider registration list must remain empty")
    configs = {x["address"]: x for x in configuration.get("root_module", {}).get("resources", [])}
    for item in plan.get("resource_changes", []):
        if item.get("mode") == "data" or item["address"] in r:
            base = item["address"].split("[", 1)[0]
            _same(configs.get(base, {}).get("provider_config_key"), "azurerm", "Azure provider alias")
    group = v["resource_group_name"]
    group_id = f'/subscriptions/{v["subscription_id"]}/resourceGroups/{group}'
    arm = lambda kind, name: group_id + "/providers/" + kind + "/" + name
    if root == "expiry":
        tags = {"managed_by":"cloud-network-benchmark", "deployment_id":v["deployment_id"]}
        _fields(r["azurerm_resource_group.attempt"], dict(name=group, location=v["location"], tags={**tags,"purpose":"independent-expiry"}), "Azure expiry group")
        account = r["azurerm_automation_account.expiry"]
        _fields(account, dict(name=group+"-auto", location=v["location"], resource_group_name=group, sku_name="Basic", tags=tags), "Azure Automation")
        _same(_one(account.get("identity"),"Automation identity").get("type"), "SystemAssigned", "Azure identity type")
        role = r["azurerm_role_definition.deallocate"]
        _same(role.get("name"),group+"-deallocate-only","Azure role name")
        from .azure_provider import ROLE_ACTIONS
        permission = _one(role.get("permissions"),"role permissions")
        _same(sorted(permission.get("actions",[])),sorted(ROLE_ACTIONS),"Azure deallocate actions")
        for key in ("not_actions","data_actions","not_data_actions"):
            if permission.get(key): raise PlanReviewError("unexpected Azure permission " + key)
        config = configs.get("azurerm_role_definition.deallocate", {})
        _link(role.get("scope"),group_id,config,"scope","azurerm_resource_group.attempt.id")
        _link(role.get("assignable_scopes"),[group_id],config,"assignable_scopes","azurerm_resource_group.attempt.id")
        assignment = r["azurerm_role_assignment.deallocate"]
        config = configs.get("azurerm_role_assignment.deallocate", {})
        _link(assignment.get("scope"),group_id,config,"scope","azurerm_resource_group.attempt.id")
        role_id = role.get("role_definition_resource_id")
        _link(assignment.get("role_definition_id"),role_id,config,"role_definition_id","azurerm_role_definition.deallocate.role_definition_resource_id")
        principal = _one(account["identity"],"identity").get("principal_id")
        _link(assignment.get("principal_id"),principal,config,"principal_id","azurerm_automation_account.expiry.identity[0].principal_id")
        _same(assignment.get("principal_type"),"ServicePrincipal","Azure role principal type")
        if assignment.get("role_definition_name") or assignment.get("condition") or assignment.get("delegated_managed_identity_resource_id"):
            raise PlanReviewError("unexpected Azure assignment scope")
        return
    tags = {"managed_by":"cloud-network-benchmark", "deployment_id":v["deployment_id"], "config_sha256":v["config_sha256"], "scenario":v["scenario"]}
    for key in (("a","b") if v["create_peering"] else ("a",)):
        region = v["region_"+key]
        cidr = "10.71.0.0" if key == "b" else "10.70.0.0"
        a = r[f'azurerm_virtual_network.pair["{key}"]']
        _fields(a, dict(name=f"{group}-{key}-vnet",location=region,resource_group_name=group,address_space=[cidr+"/16"],tags=tags),"Azure VNet")
        if a.get("subnet") or a.get("ddos_protection_plan"): raise PlanReviewError("unexpected Azure VNet attachment")
        _fields(r[f'azurerm_subnet.pair["{key}"]'],dict(name="benchmark",resource_group_name=group,virtual_network_name=f"{group}-{key}-vnet",address_prefixes=[cidr+"/24"]),"Azure subnet")
    for role in ("a","b"):
        common = dict(location=v["region_"+role],resource_group_name=group,tags=tags)
        pip = r[f'azurerm_public_ip.pair["{role}"]']
        _fields(pip,{**common,"name":f"{group}-{role}-pip","allocation_method":"Static","sku":"Standard","ip_version":"IPv4","zones":[v["zone_"+role]]},"Azure IPv4")
        nic = r[f'azurerm_network_interface.pair["{role}"]']
        _fields(nic,{**common,"name":f"{group}-{role}-nic","accelerated_networking_enabled":True,"ip_forwarding_enabled":False},"Azure NIC")
        ip = _one(nic.get("ip_configuration"),"NIC configuration")
        _fields(ip,dict(name="private",private_ip_address_allocation="Static",private_ip_address=v["private_"+role]),"Azure private IP")
        config = configs.get("azurerm_network_interface.pair",{})
        key = role if v["create_peering"] else "a"
        subnet = arm("Microsoft.Network/virtualNetworks",f"{group}-{key}-vnet") + "/subnets/benchmark"
        _link(ip.get("subnet_id"),subnet,config,"subnet_id","azurerm_subnet.pair",dynamic=True)
        _link(ip.get("public_ip_address_id"),arm("Microsoft.Network/publicIPAddresses",f"{group}-{role}-pip"),config,"public_ip_address_id","azurerm_public_ip.pair",dynamic=True)
        nsg = r[f'azurerm_network_security_group.pair["{role}"]']
        _fields(nsg,{**common,"name":f"{group}-{role}-nsg"},"Azure NSG")
        rules = nsg.get("security_rule",[])
        _same(len(rules),len(security_rules(role,v)),"Azure NSG rule count")
        for rule in security_rules(role,v):
            actual = next((a for a in rules if a.get("name")==rule["name"]),{})
            _fields(actual,rule,"Azure NSG rule")
            for field in ("source_port_ranges","destination_port_ranges","source_address_prefixes","destination_address_prefixes","source_application_security_group_ids","destination_application_security_group_ids"):
                if actual.get(field): raise PlanReviewError("unexpected Azure NSG multi-source/port rule")
        assoc = r[f'azurerm_network_interface_security_group_association.pair["{role}"]']
        config = configs.get("azurerm_network_interface_security_group_association.pair",{})
        _link(assoc.get("network_interface_id"),arm("Microsoft.Network/networkInterfaces",f"{group}-{role}-nic"),config,"network_interface_id","azurerm_network_interface.pair",dynamic=True)
        _link(assoc.get("network_security_group_id"),arm("Microsoft.Network/networkSecurityGroups",f"{group}-{role}-nsg"),config,"network_security_group_id","azurerm_network_security_group.pair",dynamic=True)
        vm = r[f'azurerm_linux_virtual_machine.pair["{role}"]']
        _fields(vm,{**common,"name":f"{group}-{role}","computer_name":role,"size":"Standard_D4s_v5","zone":v["zone_"+role],"priority":"Regular","admin_username":v["admin_username"],"disable_password_authentication":True},"Azure VM")
        config = configs.get("azurerm_linux_virtual_machine.pair",{})
        _link(vm.get("network_interface_ids"),[arm("Microsoft.Network/networkInterfaces",f"{group}-{role}-nic")],config,"network_interface_ids","azurerm_network_interface.pair",dynamic=True)
        _fields(_one(vm.get("os_disk"),"OS disk"),dict(caching="ReadWrite",storage_account_type="StandardSSD_LRS",disk_size_gb=32),"Azure OS disk")
        _fields(_one(vm.get("admin_ssh_key"),"SSH key"),dict(username=v["admin_username"],public_key=v["ssh_public_key"]),"Azure SSH key")
        if v["selected_image"].startswith("/"):
            _same(vm.get("source_image_id"),v["selected_image"],"Azure image ID")
            if vm.get("source_image_reference"): raise PlanReviewError("extra Azure image reference")
        else:
            _fields(_one(vm.get("source_image_reference"),"image reference"),dict(zip(("publisher","offer","sku","version"),v["selected_image"].split(":"))),"Azure image")
            if vm.get("source_image_id"): raise PlanReviewError("extra Azure image ID")
        if vm.get("identity") or vm.get("additional_capabilities") or vm.get("host_group_id") or vm.get("dedicated_host_id") or vm.get("capacity_reservation_group_id"):
            raise PlanReviewError("unexpected Azure VM identity/capacity scope")
        if v["create_ppg"]:
            _link(vm.get("proximity_placement_group_id"),arm("Microsoft.Compute/proximityPlacementGroups",v["placement_group_name"]),config,"proximity_placement_group_id","azurerm_proximity_placement_group.pair[0].id",condition="var.create_ppg")
        elif vm.get("proximity_placement_group_id"): raise PlanReviewError("unexpected Azure placement")
    if v["create_ppg"]:
        _fields(r["azurerm_proximity_placement_group.pair[0]"],dict(name=v["placement_group_name"],location=v["location"],resource_group_name=group,allowed_vm_sizes=["Standard_D4s_v5"],tags=tags),"Azure PPG")
    if v["create_peering"]:
        for role,other in (("a","b"),("b","a")):
            a = r[f"azurerm_virtual_network_peering.{role}_to_{other}[0]"]
            _fields(a,dict(name=f"{group}-{role}-to-{other}",resource_group_name=group,virtual_network_name=f"{group}-{role}-vnet",allow_virtual_network_access=True,allow_forwarded_traffic=False,allow_gateway_transit=False,use_remote_gateways=False),"Azure peering")
            _link(a.get("remote_virtual_network_id"),arm("Microsoft.Network/virtualNetworks",f"{group}-{other}-vnet"),configs.get(f"azurerm_virtual_network_peering.{role}_to_{other}",{}),"remote_virtual_network_id",f'azurerm_virtual_network.pair["{other}"].id')
