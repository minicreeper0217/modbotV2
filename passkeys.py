import config
import base64
import json
from datetime import datetime
import hashlib
import sqlite3
import secrets
import cbor2
import struct
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.exceptions import InvalidSignature
from dataclasses import dataclass
import uuid
import re

class PasskeyError(Exception):
	pass

def base64url_encode(data: bytes) -> str:
	return base64.urlsafe_b64encode(data).decode().rstrip("=")

def base64url_decode(data: str) -> bytes:
	if not isinstance(data, str):
		raise ValueError("Invalid Base64URL")

	if not re.fullmatch(r"[A-Za-z0-9_-]*", data):
		raise ValueError("Invalid Base64URL")

	data += "=" * (-len(data) % 4)
	return base64.urlsafe_b64decode(data)

def get_login_challage(db:sqlite3.Connection):
	challenge = secrets.token_bytes(32)
	now = datetime.now().timestamp()

	db.execute("""
		INSERT INTO passkey_challenges
		(
			challenge,
			operation,
			expires_at,
			created_at
		)
		VALUES
		(?,?,?,?)
		""",
		(
			base64url_encode(challenge),
			'login',
			int(now + 300),
			int(now),
		))
	db.commit()

	data = {
		"challenge": base64url_encode(challenge),
		"rpId": "modbot.dev",
		"timeout": 300000,
		"authenticatorSelection": {
			"residentKey": "preferred",
			"userVerification": "required"
		}
	}
	return data

def validate_login(credential:dict, db:sqlite3.Connection):

	# Decode clientDataJSON ------------------------------------------------------------------------------------

	client_data_json = base64url_decode(credential["response"]["clientDataJSON"])
	client_data = json.loads(client_data_json)

	if client_data["type"] != "webauthn.get":
		raise PasskeyError("Invalid type")

	if client_data["origin"] != f"https://{config.domain}":
		raise PasskeyError("Origin mismatch")

	if client_data["crossOrigin"]:
		raise PasskeyError("Cross-origin WebAuthn is not allowed")

	# Verify Challenge -----------------------------------------------------------------------------------------

	challenge_cur = db.execute("SELECT * FROM passkey_challenges WHERE challenge = ?",(client_data["challenge"],)).fetchone()
	now = int(datetime.now().timestamp())

	if challenge_cur is None:
		raise PasskeyError("Invalid Challenge")

	if challenge_cur[1] != "login":
		raise PasskeyError("Invalid Challenge Operation")

	if challenge_cur[2] < now:
		raise PasskeyError("Challenge Expired")

	# Find Passkey ---------------------------------------------------------------------------------------------

	passkey_cur = db.execute("SELECT public_key, sign_count FROM passkeys WHERE credential_id = ?",(base64url_decode(credential["id"]),)).fetchone()

	if passkey_cur is None:
		raise PasskeyError("Unknown credential")

	# Decode authenticatorData ---------------------------------------------------------------------------------

	auth_data = base64url_decode(credential["response"]["authenticatorData"])
	offset = 0

	if len(auth_data) < 37:
		raise PasskeyError("Invalid authenticator data")

	rp_id_hash = auth_data[offset:offset+32]
	offset += 32

	expected = hashlib.sha256(config.domain.encode()).digest()
	if rp_id_hash != expected:
		raise PasskeyError("RP ID mismatch")

	flags = auth_data[offset]
	offset += 1

	UP = bool(flags & 0x01)
	UV = bool(flags & 0x04)
	AT = bool(flags & 0x40)
	ED = bool(flags & 0x80)

	if not UP:
		raise PasskeyError("User not present")
	if not UV:
		raise PasskeyError("User verification required")
	if AT:
		raise PasskeyError("Unexpected credential data")
	if ED:
		raise PasskeyError("Unsupported extensions")

	sign_count = struct.unpack(">I",auth_data[offset:offset+4])[0]
	offset += 4

	# Verify Signature -----------------------------------------------------------------------------------------

	signed_data = (auth_data + hashlib.sha256(client_data_json).digest())
	public_key = serialization.load_der_public_key(passkey_cur[0])
	signature = base64url_decode(credential["response"]["signature"])

	try:
		public_key.verify(
			signature,
			signed_data,
			ec.ECDSA(hashes.SHA256())
		)
	except InvalidSignature:
		raise PasskeyError("Invalid signature")

	# Verify Sign Count ----------------------------------------------------------------------------------------

	stored_sign_count = passkey_cur[1]

	if stored_sign_count != 0 or sign_count != 0:
		if sign_count <= stored_sign_count:
			raise PasskeyError("Invalid sign count")

	# End Decode Credential ------------------------------------------------------------------------------------

	db.execute("UPDATE passkeys SET sign_count = ?, last_used_at = ? WHERE credential_id = ?",(sign_count, now, base64url_decode(credential["id"])))
	db.execute('DELETE FROM passkey_challenges WHERE challenge = ?', (client_data["challenge"],))
	db.commit()

	return True

def get_register_challage(db:sqlite3.Connection):
	challenge = base64url_encode(secrets.token_bytes(32))
	user_id = uuid.uuid5(config.uuid_namespace,"WhiteFoxInSnowIsCute").bytes
	now = datetime.now().timestamp()

	data = {
		"challenge": challenge,

		"rp": {
			"id": config.domain,
			"name": "modbot"
		},

		"user": {
			"id": base64url_encode(user_id),
			"name": "white fox",
			"displayName": "White Fox"
		},

		"pubKeyCredParams": [
			{
				"type": "public-key",
				"alg": -7
			}
		],

		"timeout": 300000,

		"attestation": "none",

		"authenticatorSelection": {
			"residentKey": "preferred",
			"userVerification": "required"
		}
	}

	passkeys = db.execute("SELECT credential_id FROM passkeys").fetchall()
	exclude_credentials = []
	for row in passkeys:
		exclude_credentials.append({
			"id": base64url_encode(row[0]),
			"type": "public-key"
			})
	data["excludeCredentials"] = exclude_credentials

	db.execute("""
		INSERT INTO passkey_challenges
		(
			challenge,
			operation,
			expires_at,
			created_at
		)
		VALUES
		(?,?,?,?)
		""",
		(
			challenge,
			'register',
			int(now + 300),
			int(now),
		))
	db.commit()

	return data

def register_passkey(credential:dict, db:sqlite3.Connection):

	# Decode client data ---------------------------------------------------------------------------------------

	client_data = json.loads(
		base64url_decode(
			credential["response"]["clientDataJSON"]
		)
	)

	if client_data["type"] != "webauthn.create":
		raise PasskeyError("Invalid type")

	if client_data["origin"] != f"https://{config.domain}":
		raise PasskeyError("Origin mismatch")

	if client_data["crossOrigin"]:
		raise PasskeyError("Cross-origin WebAuthn is not allowed")

	# Verify Challenge ----------------------------------------------------------------------------------------

	cur = db.execute("SELECT * FROM passkey_challenges WHERE challenge = ?", (client_data["challenge"],)).fetchone()
	if cur is None:
		raise PasskeyError("Invalid Challenge")
	if cur[1] != "register":
		raise PasskeyError("Invalid Challenge Operation")
	now = int(datetime.now().timestamp())
	if cur[2] < now:
		raise PasskeyError("Challenge Expired")

	transports = credential["response"].get("transports", [])

	# Decode authenticatorData --------------------------------------------------------------------------------

	attestation_object = base64url_decode(
		credential["response"]["attestationObject"]
	)

	try:
		attestation = cbor2.loads(attestation_object)
		auth_data = attestation["authData"]
	except (cbor2.CBORDecodeError, KeyError):
		raise PasskeyError("Invalid attestationObject")

	if len(auth_data) < 55:
		raise PasskeyError("Invalid authenticator data")

	offset = 0

	rp_id_hash = auth_data[offset:offset+32]
	offset += 32

	expected = hashlib.sha256(config.domain.encode()).digest()
	if rp_id_hash != expected:
		raise PasskeyError("RP ID mismatch")

	flags = auth_data[offset]
	offset += 1

	sign_count = struct.unpack(">I", auth_data[offset:offset+4])[0]
	offset += 4

	UP = bool(flags & 0x01)
	UV = bool(flags & 0x04)
	AT = bool(flags & 0x40)
	ED = bool(flags & 0x80)

	if not AT:
		raise PasskeyError("Invalid Credential Data")
	if not UP:
		raise PasskeyError("User not present")
	if not UV:
		raise PasskeyError("User verification required")
	if ED:
		raise PasskeyError("Unsupported extensions")

	aaguid = auth_data[offset:offset+16]
	offset += 16

	credential_length = struct.unpack(
		">H",
		auth_data[offset:offset+2]
	)[0]
	offset += 2

	if offset + credential_length > len(auth_data):
		raise PasskeyError("Invalid credential data")

	credential_id = auth_data[offset:offset+credential_length]
	offset += credential_length

	if base64url_encode(credential_id) != credential["id"]:
		raise PasskeyError("Invalid Credential ID")

	# Decode public key ---------------------------------------------------------------------------------------

	try:
		cose_key = cbor2.loads(auth_data[offset:])
	except cbor2.CBORDecodeError:
		raise PasskeyError("Invalid Cose Key")

	if not isinstance(cose_key, dict):
		raise PasskeyError("Invalid Cose Key")

	if cose_key.get(1) != 2:
		raise PasskeyError("Cose Key Not EC2 key")
	if cose_key.get(3) != -7:
		raise PasskeyError("Cose Key Not ES256")
	if cose_key.get(-1) != 1:
		raise PasskeyError("Unsupported curve")

	x = cose_key.get(-2)
	y = cose_key.get(-3)

	if not isinstance(x, bytes) or not isinstance(y, bytes):
		raise PasskeyError("Invalid EC coordinates")

	if len(x) != 32 or len(y) != 32:
		raise PasskeyError("Invalid EC coordinates")

	public_numbers = ec.EllipticCurvePublicNumbers(
		int.from_bytes(x, "big"),
		int.from_bytes(y, "big"),
		ec.SECP256R1()
	)

	try:
		public_key = public_numbers.public_key()
	except ValueError:
		raise PasskeyError("Invalid EC public key")

	public_key_der = public_key.public_bytes(
		encoding=serialization.Encoding.DER,
		format=serialization.PublicFormat.SubjectPublicKeyInfo
	)

	# End Decode Credential ------------------------------------------------------------------------------------
	
	existing = db.execute("SELECT 1 FROM passkeys WHERE credential_id = ?",(credential_id,)).fetchone()
	if existing is not None:
		raise PasskeyError("Credential already exists")

	db.execute("""
	INSERT INTO passkeys
	(
		credential_id,
		public_key,
		name,
		sign_count,
		transports,
		created_at,
		aaguid
	)
	VALUES
	(?,?,?,?,?,?,?)
	""",
	(
		credential_id,
		public_key_der,
		"新的 Passkey",
		sign_count,
		json.dumps(transports),
		int(datetime.now().timestamp()),
		aaguid
	))
	db.execute('DELETE FROM passkey_challenges WHERE challenge = ?', (client_data["challenge"],))
	db.commit()

	return True

def validate_webauthn_credential(credential: dict, assertion: bool) -> str | None:

	if not isinstance(credential, dict):
		return "credential must be an object"

	if not isinstance(credential.get("id"), str):
		return "missing credential.id"

	if credential.get("type") != "public-key":
		return "invalid credential.type"

	response = credential.get("response")

	if not isinstance(response, dict):
		return "missing credential.response"

	required = ["clientDataJSON"]

	if assertion:
		required += [
			"authenticatorData",
			"signature"
		]
	else:
		required += [
			"attestationObject"
		]

	for key in required:

		value = response.get(key)

		if not isinstance(value, str):
			return f"missing response.{key}"

		try:
			base64url_decode(value)
		except (ValueError, TypeError):
			return f"invalid response.{key}"

	try:
		base64url_decode(credential["id"])
	except (ValueError, TypeError):
		return "invalid credential.id"

	error = validate_client_data(response["clientDataJSON"])
	if error:
		return error

	return None

def validate_client_data(data: str) -> str | None:
	try:
		raw = base64url_decode(data)
		client_data = json.loads(raw)
	except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
		return "invalid clientDataJSON"

	if not isinstance(client_data, dict):
		return "invalid clientDataJSON"

	if not isinstance(client_data.get("type"), str):
		return "invalid clientDataJSON.type"

	if not isinstance(client_data.get("challenge"), str):
		return "invalid clientDataJSON.challenge"

	if not isinstance(client_data.get("origin"), str):
		return "invalid clientDataJSON.origin"

	if not isinstance(client_data.get("crossOrigin"), bool):
		return "invalid clientDataJSON.crossOrigin"

	return None