import config
import aiohttp
import json
import os

# for page
INVESTIGATING = "investigating" # 調查中
IDENTIFIED = "identified" # 已識別問題
MONITORING = "monitoring" # 監控中
RESOLVED = "resolved" # 已解決

# for component
OPERATIONAL = "operational" # 運作中
MAINTENANCE = "under_maintenance" # 維護中
PARTIAL_OUTAGE = "partial_outage" # 部分中斷
MAJOR_OUTAGE = "major_outage" # 完全中斷

async def create_incident(name:str, page_status:str, component_id:str, component_status:str, message:str, reminder:bool = True) -> dict:
	with open(os.path.join(config.dir, 'data','status_page.json'), 'r') as f:
		json_data = json.load(f)
		if not json_data.get(component_id):
			raise ValueError(f"Component id not found: {component_id}")
		elif json_data[component_id]["incidents"]:
			raise RuntimeError(f'Component {json_data[component_id]["name"]} have a incident: {json_data[component_id]["incidents"]}')
	if reminder:
		reminder_intervals = "8"
	else:
		reminder_intervals = "[]"
	headers = {
		"Content-Type": "application/json",
		"Authorization": f"OAuth {config.status_page_key}"
	}
	data = {
		"incident":{
				"name": name,
				"status": page_status,
				"components":{
						component_id: component_status
				},
				"component_ids":[
						component_id
				],
				"body": message,
				"reminder_intervals": reminder_intervals
		}
	}
	async with aiohttp.ClientSession() as s:
		async with s.post(f"https://api.statuspage.io/v1/pages/{config.status_page_id}/incidents", data=json.dumps(data), headers=headers) as r:
			r.raise_for_status()
			incident_data = await r.json()
			incident_id = incident_data["id"]
			json_data[component_id]["incidents"] = incident_id
			with open(os.path.join(config.dir, 'data','status_page.json'), 'w') as f:
				json.dump(json_data, f, indent=2)
			return incident_data

async def update_incident(page_status:str, component_id:str, component_status:str, message:str) -> dict:
	with open(os.path.join(config.dir, 'data','status_page.json'), 'r') as f:
		json_data = json.load(f)
		if not json_data.get(component_id):
			raise ValueError(f"Component id not found: {component_id}")
		elif not json_data[component_id]["incidents"]:
			raise RuntimeError(f"Component {json_data[component_id]['name']} doesn't have a incident")
		incident_id = json_data[component_id]["incidents"]
	headers = {
		"Content-Type": "application/json",
		"Authorization": f"OAuth {config.status_page_key}"
	}
	data = {
		"incident":{
				"status": page_status,
				"components":{
						component_id: component_status
				},
				"component_ids":[
						component_id
				],
				"body": message
		}
	}
	async with aiohttp.ClientSession() as s:
		async with s.patch(f"https://api.statuspage.io/v1/pages/{config.status_page_id}/incidents/{incident_id}", data=json.dumps(data), headers=headers) as r:
			r.raise_for_status()
			incident_data = await r.json()
			if page_status == RESOLVED:
				json_data[component_id]["incidents"] = None
				with open(os.path.join(config.dir, 'data','status_page.json'), 'w') as f:
					json.dump(json_data, f, indent=2)
			return incident_data

async def get_incident(component_id:str, incident_id:str | None = None) -> dict:
	if incident_id is None:
		with open(os.path.join(config.dir, 'data','status_page.json'), 'r') as f:
			json_data = json.load(f)
			if not json_data.get(component_id):
				raise ValueError(f"Component id not found: {component_id}")
			elif not json_data[component_id]["incidents"]:
				raise ValueError(f"Component {json_data[component_id]['name']} doesn't have a incident")
			incident_id = json_data[component_id]["incidents"]
	headers = {
		"Authorization": f"OAuth {config.status_page_key}"
	}
	async with aiohttp.ClientSession() as s:
		async with s.get(f"https://api.statuspage.io/v1/pages/{config.status_page_id}/incidents/{incident_id}", headers=headers) as r:
			r.raise_for_status()
			incident_data = await r.json()
			return incident_data

async def delete_incident(component_id:str, incident_id:str) -> dict:
	headers = {
		"Authorization": f"OAuth {config.status_page_key}"
	}
	async with aiohttp.ClientSession() as s:
		async with s.delete(f"https://api.statuspage.io/v1/pages/{config.status_page_id}/incidents/{incident_id}", headers=headers) as r:
			r.raise_for_status()
			incident_data = await r.json()
			with open(os.path.join(config.dir, 'data','status_page.json'), 'r+') as f:
				json_data = json.load(f)
				if not json_data.get(component_id):
					pass
				elif json_data[component_id]["incidents"] == incident_id:
					json_data[component_id]["incidents"] = None
					f.seek(0)
					json.dump(json_data, f, indent=2)
					f.truncate()
			return incident_data
		
def get_component_id(component_name:str) -> str:
	with open(os.path.join(config.dir, 'data','status_page.json'), 'r') as f:
		json_data = json.load(f)
	component_id = None
	for key, value in json_data.items():
		if value["name"] == component_name:
			component_id = key
			break
	if component_id is None:
		raise ValueError(f"{component_name} doesn't have status page component id")
	return component_id