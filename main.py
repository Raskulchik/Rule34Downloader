import os
import aiohttp
import asyncio
import random
from aiohttp import ClientSession
from urllib.parse import quote

MAX_CONNECTIONS = 5
RETRY_LIMIT = 5
DOWNLOAD_CHUNK_SIZE = 100

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0"
}


def parse_tags_input(raw_tags: str) -> str:
    tags = raw_tags.strip().split()
    return "+".join([quote(tag) for tag in tags])


async def fetch_with_retries(session, url, retries=RETRY_LIMIT):
    for attempt in range(1, retries + 1):
        try:
            async with session.get(url, headers=HEADERS) as resp:
                if resp.status in [429, 503]:
                    print(f"Error {resp.status}, attempt {attempt}")
                    await asyncio.sleep(5 * attempt)
                    continue
                if resp.status != 200:
                    print(f"Error {resp.status}, attempt {attempt}")
                    await asyncio.sleep(2)
                    continue
                return await resp.json()
        except Exception as e:
            print(f"Error {e}, attempt {attempt}")
            await asyncio.sleep(2)
    return None


async def download_file(session: ClientSession, url: str, filepath: str):
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            await asyncio.sleep(random.uniform(1.5, 3.0))
            async with session.get(url, headers=HEADERS) as resp:
                if resp.status in [429, 503]:
                    print(f"Error {resp.status}, attempt {attempt}")
                    await asyncio.sleep(5 * attempt)
                    continue
                if resp.status != 200:
                    print(f"Error {resp.status}, attempt {attempt}")
                    await asyncio.sleep(2)
                    continue

                with open(filepath, "wb") as f:
                    while True:
                        chunk = await resp.content.read(1024)
                        if not chunk:
                            break
                        f.write(chunk)
                return
        except Exception as e:
            print(f"Failed to download {url}: {e}, attempt {attempt}")
            await asyncio.sleep(2)


async def download_all(posts, folder):
    os.makedirs(folder, exist_ok=True)
    sem = asyncio.Semaphore(MAX_CONNECTIONS)

    async with aiohttp.ClientSession() as session:
        tasks = []

        for post in posts:
            file_url = post.get("file_url")
            if not file_url:
                continue

            filename = file_url.split("/")[-1]
            filepath = os.path.join(folder, filename)

            if os.path.exists(filepath):
                continue

            async def bound_download(url=file_url, path=filepath):
                async with sem:
                    await download_file(session, url, path)

            tasks.append(bound_download())

        await asyncio.gather(*tasks)


async def fetch_posts(tags, limit=0):
    print("Getting post list")
    all_posts = []
    page = 0
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(random.uniform(1.5, 3.0))
            page += 1
            url = f"https://rule34.xxx/index.php?page=dapi&s=post&q=index&json=1&tags={tags}&pid={page}&limit=100"
            data = await fetch_with_retries(session, url)

            if not data:
                break

            all_posts.extend(data)

            if len(data) < 100:
                break
            if limit > 0 and len(all_posts) >= limit:
                break

    print(f"Found {len(all_posts)} posts")
    if limit > 0:
        all_posts = all_posts[:limit]

    # 🔽 Подтверждение перед началом скачивания
    confirm = input(f"Start download {len(all_posts)} posts? (y/n): ").strip().lower()
    if confirm != 'y':
        print("❌ Cancelled by user.")
        return []

    return all_posts


async def main():
    raw_tags = input("Any tags? (example: Miyabi -ai_generated): ")
    tag_folder_name = raw_tags.replace(" ", "_")
    tag_string = parse_tags_input(raw_tags)

    count_input = input("How many posts to download? (0 or Enter = all): ").strip()
    count = int(count_input) if count_input else 0

    posts = await fetch_posts(tag_string, count)
    if not posts:
        print("Nothing found or cancelled.")
        return

    print("Starting download...\n")

    for i in range(0, len(posts), DOWNLOAD_CHUNK_SIZE):
        chunk = posts[i:i + DOWNLOAD_CHUNK_SIZE]
        await download_all(chunk, tag_folder_name)
        await asyncio.sleep(random.uniform(10, 20))  # пауза между партиями

    print("\n✅ Download complete!")


if __name__ == "__main__":
    asyncio.run(main())
