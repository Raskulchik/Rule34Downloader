import os
import aiohttp
import asyncio
import random
from aiohttp import ClientSession
from urllib.parse import quote
from tqdm.asyncio import tqdm_asyncio  # Progress bar

MAX_CONNECTIONS = 5
RETRY_LIMIT = 5
DOWNLOAD_CHUNK_SIZE = 100
PAGE_SIZE = 100  # Number of posts per API request

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0"
}

def parse_tags_input(raw_tags: str) -> str:
    tags = raw_tags.strip().split()
    encoded_tags = []
    for tag in tags:
        if tag.startswith('-') and len(tag) > 1:
            encoded_tags.append('-' + quote(tag[1:]))
        else:
            encoded_tags.append(quote(tag))
    return "+".join(encoded_tags)


async def fetch_with_retries(session, url, retries=RETRY_LIMIT):
    for attempt in range(1, retries + 1):
        try:
            async with session.get(url, headers=HEADERS) as resp:
                if resp.status in [429, 503]:
                    print(f"Error {resp.status}, retry {attempt}")
                    await asyncio.sleep(5 * attempt)
                    continue
                if resp.status != 200:
                    print(f"Error {resp.status}, retry {attempt}")
                    await asyncio.sleep(2)
                    continue
                return await resp.json()
        except Exception as e:
            print(f"Error {e}, retry {attempt}")
            await asyncio.sleep(2)
    return None


async def download_file(session: ClientSession, url: str, filepath: str):
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            await asyncio.sleep(random.uniform(1.5, 3.0))
            async with session.get(url, headers=HEADERS) as resp:
                if resp.status in [429, 503]:
                    print(f"Error {resp.status}, retry {attempt}")
                    await asyncio.sleep(5 * attempt)
                    continue
                if resp.status != 200:
                    print(f"Error {resp.status}, retry {attempt}")
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
            print(f"Failed to download {url}: {e}, retry {attempt}")
            await asyncio.sleep(2)


async def download_all(posts, folder):
    os.makedirs(folder, exist_ok=True)
    sem = asyncio.Semaphore(MAX_CONNECTIONS)

    async with aiohttp.ClientSession() as session:
        async def bound_download(post):
            file_url = post.get("file_url")
            if not file_url:
                return

            filename = file_url.split("/")[-1]
            filepath = os.path.join(folder, filename)
            if os.path.exists(filepath):
                return

            async with sem:
                await download_file(session, file_url, filepath)

        await tqdm_asyncio.gather(*(bound_download(post) for post in posts), desc="Downloading")


async def fetch_posts(tags: str):
    print("Getting post list")
    all_posts = []
    page = 0
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(random.uniform(1.5, 3.0))
            url = (
                f"https://rule34.xxx/index.php?page=dapi&s=post&q=index&json=1"
                f"&tags={tags}&pid={page}&limit={PAGE_SIZE}"
            )
            data = await fetch_with_retries(session, url)
            if not data:
                break
            all_posts.extend(data)
            print(f"  Retrieved {len(data)} posts (page {page})")
            if len(data) < PAGE_SIZE:
                break
            page += 1

    print(f"Found {len(all_posts)} posts total")
    return all_posts


async def main():
    raw_tags = input("Any tags? (example: Miyabi -ai_generated): ")
    tag_folder_name = raw_tags.replace(" ", "_")
    tag_string = parse_tags_input(raw_tags)

    posts = await fetch_posts(tag_string)
    if not posts:
        print("Nothing found.")
        return

    confirm = input(f"Start download {len(posts)} posts? (y/n): ").strip().lower()
    if confirm != 'y':
        print("❌ Cancelled by user.")
        return

    count_input = input("How many posts to download? (0 or Enter = all): ").strip()
    count = int(count_input) if count_input else 0
    if count > 0:
        posts = posts[:count]

    print("Starting download...\n")
    for i in range(0, len(posts), DOWNLOAD_CHUNK_SIZE):
        chunk = posts[i:i + DOWNLOAD_CHUNK_SIZE]
        await download_all(chunk, tag_folder_name)
        await asyncio.sleep(random.uniform(10, 20))

    print("\n✅ Download complete!")


if __name__ == "__main__":
    asyncio.run(main())
