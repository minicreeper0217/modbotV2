"""Read-only asynchronous GitHub REST API client for the c3 coding assistant."""

import asyncio
import base64
import binascii
import re

from urllib.parse import quote

import aiohttp


class GitHubError(Exception):
  """A safe, human-readable GitHub API error."""


class GitHub:
  API_URL = "https://api.github.com"
  MAX_FILE_BYTES = 512 * 1024
  MAX_SEARCH_FILES = 100

  def __init__(self, token: str | None = None, timeout: int = 30):
    self.token = token
    self.timeout = aiohttp.ClientTimeout(total=timeout)

  @staticmethod
  def _repo(repo: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
      raise ValueError("repo 必須是 owner/repository 格式")
    return "/".join(quote(part, safe="") for part in repo.split("/"))

  @staticmethod
  def _path(path: str) -> str:
    parts = path.strip("/").split("/") if path else []
    if any(part in (".", "..") for part in parts):
      raise ValueError("不允許使用相對路徑")
    return "/".join(quote(part, safe="") for part in parts)

  async def _request(self, endpoint: str, params: dict | None = None):
    headers = {
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "modbotV2-c3"
    }
    if self.token:
      headers["Authorization"] = f"Bearer {self.token}"

    async with aiohttp.ClientSession(timeout=self.timeout, headers=headers) as session:
      try:
        async with session.get(f"{self.API_URL}/{endpoint}", params=params, allow_redirects=False) as response:
          if response.status != 200:
            details = (await response.text())[:500]
            raise GitHubError(f"GitHub HTTP {response.status}: {details}")
          return await response.json()
      except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise GitHubError(f"GitHub 連線失敗: {type(e).__name__}") from e

  async def repository(self, repo: str) -> dict:
    data = await self._request(f"repos/{self._repo(repo)}")
    return {
      "name": data["full_name"],
      "description": data.get("description"),
      "default_branch": data["default_branch"],
      "private": data["private"],
      "html_url": data["html_url"],
      "updated_at": data.get("updated_at")
    }

  async def contents(self, repo: str, path: str = "", ref: str | None = None):
    endpoint = f"repos/{self._repo(repo)}/contents"
    if path:
      endpoint += "/" + self._path(path)
    return await self._request(endpoint, {"ref": ref} if ref else None)

  async def list_directory(self, repo: str, path: str = "", ref: str | None = None) -> list:
    data = await self.contents(repo, path, ref)
    if not isinstance(data, list):
      raise GitHubError("指定路徑不是資料夾")
    return [
      {"name": item["name"], "path": item["path"], "type": item["type"], "size": item.get("size")}
      for item in data
    ]

  async def read_file(self, repo: str, path: str, ref: str | None = None) -> dict:
    data = await self.contents(repo, path, ref)
    if not isinstance(data, dict) or data.get("type") != "file":
      raise GitHubError("指定路徑不是一般檔案")
    if data.get("size", 0) > self.MAX_FILE_BYTES:
      raise GitHubError("檔案過大，請改用較小的檔案")
    if data.get("encoding") != "base64" or not data.get("content"):
      raise GitHubError("GitHub 未提供可解碼的檔案內容")
    try:
      raw = base64.b64decode(data["content"], validate=False)
      if len(raw) > self.MAX_FILE_BYTES:
        raise GitHubError("檔案過大")
      content = raw.decode("utf-8")
    except (UnicodeDecodeError, binascii.Error) as e:
      raise GitHubError("檔案不是 UTF-8 文字檔") from e
    return {"path": data["path"], "sha": data["sha"], "size": len(raw), "content": content}

  async def branches(self, repo: str, page: int = 1, per_page: int = 100) -> list:
    data = await self._request(f"repos/{self._repo(repo)}/branches", self._page(page, per_page))
    return [{"name": item["name"], "sha": item["commit"]["sha"]} for item in data]

  async def commits(self, repo: str, ref: str | None = None, path: str | None = None,
                    page: int = 1, per_page: int = 30) -> list:
    params = self._page(page, per_page)
    if ref:
      params["sha"] = ref
    if path:
      params["path"] = path
    data = await self._request(f"repos/{self._repo(repo)}/commits", params)
    return [
      {"sha": item["sha"], "message": item["commit"]["message"],
       "date": item["commit"]["author"].get("date"), "url": item["html_url"]}
      for item in data
    ]

  async def commit(self, repo: str, sha: str) -> dict:
    data = await self._request(f"repos/{self._repo(repo)}/commits/{quote(sha, safe='')}")
    return {
      "sha": data["sha"], "message": data["commit"]["message"],
      "stats": data.get("stats"), "url": data["html_url"],
      "files": [{"path": f["filename"], "status": f["status"],
                 "additions": f["additions"], "deletions": f["deletions"],
                 "patch": f.get("patch", "")[:12000]} for f in data.get("files", [])]
    }

  async def compare(self, repo: str, base: str, head: str) -> dict:
    endpoint = f"repos/{self._repo(repo)}/compare/{quote(base, safe='')}...{quote(head, safe='')}"
    data = await self._request(endpoint)
    return {
      "status": data["status"], "ahead_by": data["ahead_by"],
      "behind_by": data["behind_by"], "total_commits": data["total_commits"],
      "files": [{"path": f["filename"], "status": f["status"],
                 "additions": f["additions"], "deletions": f["deletions"],
                 "patch": f.get("patch", "")[:12000]} for f in data.get("files", [])]
    }

  async def tree(self, repo: str, ref: str, recursive: bool = True) -> dict:
    params = {"recursive": "1"} if recursive else None
    data = await self._request(
      f"repos/{self._repo(repo)}/git/trees/{quote(ref, safe='')}", params
    )
    return {
      "truncated": data.get("truncated", False),
      "files": [{"path": item["path"], "type": item["type"], "size": item.get("size")}
                for item in data.get("tree", [])]
    }

  async def search_paths(self, repo: str, ref: str, keyword: str, limit: int = 50) -> dict:
    """Search file paths in a specific branch; not file contents."""
    if not keyword:
      raise ValueError("keyword 不可為空")
    result = await self.tree(repo, ref)
    paths = [f for f in result["files"] if f["type"] == "blob" and keyword.lower() in f["path"].lower()]
    return {"matches": paths[:min(max(limit, 1), 100)], "truncated": result["truncated"]}

  async def search_code(self, repo: str, query: str, page: int = 1, per_page: int = 30) -> dict:
    """GitHub code search; normally searches the repository's default branch."""
    if not query.strip():
      raise ValueError("query 不可為空")
    params = self._page(page, per_page)
    params["q"] = f"{query} repo:{repo}"
    data = await self._request("search/code", params)
    return {
      "total_count": data["total_count"],
      "incomplete_results": data.get("incomplete_results", False),
      "items": [{"name": item["name"], "path": item["path"], "sha": item["sha"]}
                for item in data.get("items", [])]
    }

  @staticmethod
  def _page(page: int, per_page: int) -> dict:
    if page < 1 or not 1 <= per_page <= 100:
      raise ValueError("page 必須 >= 1，per_page 必須介於 1 到 100")
    return {"page": page, "per_page": per_page}
