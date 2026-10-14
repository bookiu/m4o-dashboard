import pytest
from aiohttp import web

from m4o_dashboard.api import APIError, MihomoClient
from m4o_dashboard.config import Settings
from m4o_dashboard.models import DashboardState


async def test_rules_endpoint_preserves_order_payloads_and_names(client, demo):
    rules = await client.rules()
    assert rules == demo.rules
    state = DashboardState()
    state.update_rules(rules)
    assert [rule.index for rule in state.rules] == [1, 2, 3, 4, 5, 6]
    assert state.rules[0].kind == "DOMAIN-SUFFIX"
    assert state.rules[2].kind == "GEOIP"
    assert state.rules[3].kind == "IP-CIDR"
    assert state.rules[-1].payload == "*"
    assert state.rules[0].proxy == demo.group
    assert state.rules[0].matches("GITHUB")
    assert state.rules[0].matches(demo.group)
    demo.rules.clear()
    assert await client.rules() == []


@pytest.mark.parametrize("value", [None, {}, [False], ["rule"]])
async def test_malformed_rule_responses_are_rejected(serve, value):
    async def rules(request):
        return web.json_response({"rules": value})

    server = web.Application()
    server.router.add_get("/rules", rules)
    url = await serve(server)
    async with MihomoClient(Settings(url=url)) as client:
        with pytest.raises(APIError, match="Invalid rule list"):
            await client.rules()


async def test_config_patch_updates_supported_fields_only(client, demo):
    changes = {
        "mixed-port": 7891,
        "allow-lan": True,
        "ipv6": False,
        "log-level": "warning",
        "find-process-mode": "off",
    }
    await client.update_configs(changes)
    assert demo.config_values == changes
    assert demo.mode == "rule"
    before = list(demo.calls)
    await client.update_configs({})
    assert demo.calls == before


@pytest.mark.parametrize(
    "changes",
    [
        {"mixed-port": True},
        {"mixed-port": -1},
        {"mixed-port": 65536},
        {"mixed-port": "7890"},
        {"mixed-port": 1.5},
        {"allow-lan": "false"},
        {"ipv6": 1},
        {"log-level": "trace"},
        {"find-process-mode": "sometimes"},
        {"secret": "do-not-send"},
        {"mixed-port": 7891, "unknown": True},
    ],
)
async def test_invalid_config_patch_sends_no_request(client, demo, changes):
    before = dict(demo.config_values)
    with pytest.raises(APIError, match="Invalid or unsupported config field"):
        await client.update_configs(changes)
    assert demo.config_values == before
    assert not demo.calls
