from time import perf_counter
from functools import wraps
import json
import config
import os

def timer(func):
	@wraps(func)
	async def counter(*args, **kwargs):
		start = perf_counter()
		await func(*args, **kwargs)
		end = perf_counter()
		with open(os.path.join(config.dir, 'data', 'logs', 'timer.json'), 'r+') as t:
			name = f"{func.__name__}"
			timer = json.load(t)
			timer.setdefault(name, [])
			timer['average'].setdefault(name, 0)
			timer['max'].setdefault(name, 0)
			timer['min'].setdefault(name, 0)
			timer[name].append(end - start)
			if len(timer[name]) > 15:
				timer[name] = timer[name][-15:]
			total = sum(timer[name])
			average = total / len(timer[name])
			timer['average'][name] = average
			if timer['min'][name] > (end - start):
				timer['min'][name] = (end - start)
			elif timer['max'][name]	< (end - start):
				timer['max'][name] = (end - start)
			t.seek(0)
			json.dump(timer, t, indent=2)
			t.truncate()
	return counter

async def special(name:str, time:float):
	with open(os.path.join(config.dir, 'data', 'logs', 'timer.json'), 'r+') as t:
		timer = json.load(t)
		timer.setdefault(name, [])
		timer['average'].setdefault(name, 0)
		timer['max'].setdefault(name, 0)
		timer['min'].setdefault(name, 0)
		timer[name].append(time)
		if len(timer[name]) > 15:
			timer[name] = timer[name][-15:]
		total = sum(timer[name])
		average = total / len(timer[name])
		timer['average'][name] = average
		if timer['min'][name] > (time):
			timer['min'][name] = (time)
		elif timer['max'][name]	< (time):
			timer['max'][name] = (time)
		t.seek(0)
		json.dump(timer, t, indent=2)
		t.truncate()