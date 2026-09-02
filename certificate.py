from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from datetime import datetime, timedelta
import subprocess
import aiohttp
import json
import config
import os
import pytz
import logging

tz = pytz.utc

def generate_ca():
	key = ec.generate_private_key(ec.SECP384R1())

	subject = x509.Name([
		x509.NameAttribute(NameOID.COUNTRY_NAME, "JP"),
		x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Neko House"),
		x509.NameAttribute(NameOID.COMMON_NAME, "Neko Root CA"),
	])

	certificate_id = x509.random_serial_number()
	year_now = datetime.now().year

	cert = (
		x509.CertificateBuilder()
		.subject_name(subject)
		.issuer_name(subject)
		.public_key(key.public_key())
		.serial_number(certificate_id)
		.not_valid_before(datetime(year_now, 1, 1, tzinfo=tz))
		.not_valid_after(datetime(year_now + 1, 2, 1, tzinfo=tz))
		.add_extension(
			x509.BasicConstraints(ca=True, path_length=None),
			critical=True
		)
		.add_extension(
			x509.KeyUsage(
				digital_signature=True,
				content_commitment=False,
				key_encipherment=False,
				data_encipherment=False,
				key_agreement=False,
				key_cert_sign=True,
				crl_sign=True,
				encipher_only=False,
				decipher_only=False
			),
			critical=True
		)
		.add_extension(
			x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
			critical=False
		)
		.add_extension(
			x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
			critical=False
		)
		.sign(key, hashes.SHA256())
	)

	private_key = key.private_bytes(
		serialization.Encoding.PEM,
		serialization.PrivateFormat.PKCS8,
		serialization.NoEncryption()
	).decode()

	certificate = cert.public_bytes(
		serialization.Encoding.PEM
	).decode()

	fingerprint = cert.fingerprint(hashes.SHA256()).hex()

	return certificate, certificate_id, private_key, fingerprint, int(cert.not_valid_after.timestamp())


def update_ca(certificate: str, private_key: str):
	with open(os.path.join(config.nginx_certificate_path, "ca_cert.pem"), "w") as f:
		f.write(certificate)

	with open(os.path.join(config.nginx_certificate_path, "ca_key.pem"), "w") as f:
		f.write(private_key)

def generate_leaf_certificate(common_name: str, eku):
	with open(os.path.join(config.nginx_certificate_path, "ca_key.pem"), "rb") as f:
		ca_private_key = serialization.load_pem_private_key(f.read(), password=None)

	with open(os.path.join(config.nginx_certificate_path, "ca_cert.pem"), "rb") as f:
		ca_cert = x509.load_pem_x509_certificate(f.read())

	key = ec.generate_private_key(ec.SECP256R1())

	subject = x509.Name([
		x509.NameAttribute(NameOID.COUNTRY_NAME, "JP"),
		x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Neko House"),
		x509.NameAttribute(NameOID.COMMON_NAME, common_name),
	])

	certificate_id = x509.random_serial_number()

	cert = (
		x509.CertificateBuilder()
		.subject_name(subject)
		.issuer_name(ca_cert.subject)
		.public_key(key.public_key())
		.serial_number(certificate_id)
		.not_valid_before(datetime.now(tz=tz) - timedelta(minutes=5))
		.not_valid_after(datetime.now(tz=tz) + timedelta(days=90) - timedelta(minutes=5))
		.add_extension(
			x509.BasicConstraints(ca=False, path_length=None),
			critical=True
		)
		.add_extension(
			x509.KeyUsage(
				digital_signature=True,
				content_commitment=False,
				key_encipherment=True,
				data_encipherment=False,
				key_agreement=False,
				key_cert_sign=False,
				crl_sign=False,
				encipher_only=False,
				decipher_only=False
			),
			critical=True
		)
		.add_extension(
			x509.ExtendedKeyUsage([eku]),
			critical=False
		)
		.add_extension(
			x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
			critical=False
		)
		.add_extension(
			x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_private_key.public_key()),
			critical=False
		)
		.sign(ca_private_key, hashes.SHA256())
	)

	private_key = key.private_bytes(
		serialization.Encoding.PEM,
		serialization.PrivateFormat.PKCS8,
		serialization.NoEncryption()
	).decode()

	certificate = cert.public_bytes(
		serialization.Encoding.PEM
	).decode()

	fingerprint = cert.fingerprint(hashes.SHA256()).hex()

	return (
		certificate,
		certificate_id,
		private_key,
		fingerprint,
		int(cert.not_valid_after.timestamp() + 28800)
	)

def generate_cloudflare_origin_tls_auth():
	return generate_leaf_certificate(
		"Cloudflare Origin TLS Auth",
		x509.OID_CLIENT_AUTH,
	)

def generate_ragdoll():
	return generate_leaf_certificate(
		"Ragdoll",
		x509.OID_SERVER_AUTH
	)

async def get_origin_tls_auth(cloudflare_id: str, s: aiohttp.ClientSession, zone_id:str) -> dict:
	header = {
		"Authorization": f"Bearer {config.cloudflare_origin_ca_key}",
	}

	async with s.get(f"https://api.cloudflare.com/client/v4/zones/{zone_id}/origin_tls_client_auth/{cloudflare_id}", headers=header) as r:
		r.raise_for_status()
		return await r.json()

async def update_origin_tls_auth(certificate: str, private_key: str, s: aiohttp.ClientSession, zone_id:str):
	header = {
		"Authorization": f"Bearer {config.cloudflare_origin_ca_key}",
		"Content-Type": "application/json"
	}

	data = {
		"certificate": certificate,
		"private_key": private_key
	}

	async with s.post(
		f"https://api.cloudflare.com/client/v4/zones/{zone_id}/origin_tls_client_auth",data=json.dumps(data), headers=header) as r:
		r.raise_for_status()
		return await r.json()

async def revoke_origin_tls_auth(cloudflare_id: str, s: aiohttp.ClientSession, zone_id:str):
	header = {
		"Authorization": f"Bearer {config.cloudflare_origin_ca_key}",
	}

	async with s.delete(f"https://api.cloudflare.com/client/v4/zones/{zone_id}/origin_tls_client_auth/{cloudflare_id}", headers=header) as r:
		r.raise_for_status()

def update_ragdoll(certificate: str, private_key: str):
	with open(os.path.join(config.nginx_certificate_path, "ragdoll_cert.pem"), "w") as f:
		f.write(certificate)

	with open(os.path.join(config.nginx_certificate_path, "ragdoll_key.pem"), "w") as f:
		f.write(private_key)

	subprocess.run("sudo nginx -t", shell=True, check=True, text=True)
	subprocess.run("sudo nginx -s reload", shell=True, check=True, text=True)

def generate_csr():
	'''
	`Return:`
	CSR, Private Key, Public Key, Finger Print
	'''

	key = ec.generate_private_key(ec.SECP256R1(), default_backend())

	csr = x509.CertificateSigningRequestBuilder().subject_name(x509.Name([
		x509.NameAttribute(NameOID.COUNTRY_NAME, u"JP"),
		x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, u"Tokyo"),
		x509.NameAttribute(NameOID.COMMON_NAME, "Origin Certificate"),
	])).sign(key, hashes.SHA256(), default_backend())

	private_key = key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())

	public_key = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

	csr_pem = csr.public_bytes(serialization.Encoding.PEM)

	digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
	digest.update(key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo))
	fingerprint = digest.finalize()

	return csr_pem.decode(), private_key.decode(), public_key.decode(), fingerprint.hex()

async def sign_certificate(csr:str, s:aiohttp.ClientSession, domain:str) -> dict:
	header = {
		"Authorization": f"Bearer {config.cloudflare_origin_ca_key}",
		"Content-Type": "application/json"
	}
	data = {
		"csr": csr,
		"hostnames": [domain, f"*.{domain}"],
		"requested_validity": 90,
		"request_type": "origin-ecc"
	}
	logging.info(json.dumps(data, indent=2))
	async with s.post("https://api.cloudflare.com/client/v4/certificates", data=json.dumps(data), headers=header) as r:
		r.raise_for_status()
		return await r.json()

async def revoke_certificate(id:str, s:aiohttp.ClientSession) -> None:
	header = {
		"Authorization": f"Bearer {config.cloudflare_origin_ca_key}",
	}
	async with s.delete(f"https://api.cloudflare.com/client/v4/certificates/{id}", headers=header) as r:
		r.raise_for_status()

def update_nginx(certificate:str, private_key:str, cert_path:str):
	with open(os.path.join(cert_path, "chain.pem"), "w") as f:
		f.write(certificate)
	with open(os.path.join(cert_path, "key.pem"), "w") as f:
		f.write(private_key)
	subprocess.run("sudo nginx -t", shell=True, check=True, text=True)
	subprocess.run("sudo nginx -s reload", shell=True, check=True, text=True)
