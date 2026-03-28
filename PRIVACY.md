# Privacy Policy

**Last updated: March 28, 2026**

## Overview

AI Music Publisher ("the Application") is a personal automation tool that generates AI-based music videos and publishes them to social media platforms including TikTok, on behalf of the account owner only.

## Data Collection

The Application does **not** collect, store, or process any personal data from third-party users.

- No user registration or login is required
- No personal information is collected from visitors or third parties
- No cookies or tracking technologies are used on users

## TikTok API Usage

The Application uses the TikTok Content Posting API solely to publish video content to the account owner's own TikTok account. The following data is used:

- **OAuth Access Token**: Used to authenticate API requests on behalf of the account owner. Stored locally in environment variables and never shared.
- **TikTok Open ID**: The account identifier of the authorized TikTok account owner. Used only for API authentication.

No data obtained through the TikTok API is shared with any third party.

## Data Storage

API credentials (access tokens, refresh tokens) are stored securely as environment variables and GitHub Actions secrets. They are never logged, exposed, or transmitted to any party other than TikTok's official API endpoints.

## Third-Party Services

The Application interacts with the following third-party APIs:

- **TikTok API** (open.tiktokapis.com) — for video publishing
- **YouTube Data API** — for video publishing
- **OpenAI API** — for content generation
- **Suno API** — for AI music generation

Each service is governed by its own privacy policy.

## Contact

This application is operated as a personal project by the repository owner.
GitHub: [https://github.com/jaeil-park/ai-music-publisher](https://github.com/jaeil-park/ai-music-publisher)
