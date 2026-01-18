# Broken Image Detection

A production-style pipeline for detecting **broken, duplicate, blurry, placeholder, and low-quality images** at scale using computer vision heuristics and robust engineering patterns.

This repository focuses on **how the problem is engineered**, not on providing a plug-and-play application.

---

## 🔍 Problem Overview

Large image catalogs often suffer from:
- Broken or unreachable image URLs
- Placeholder or grey images
- Duplicate images across different products
- Blurry or low-quality images
- Non-standard image dimensions

Manually identifying these issues at scale is error-prone and time-consuming.

---

## 🧠 Solution Approach

This project demonstrates a scalable and resilient approach to image quality validation using:

- **HTTP retry & backoff strategies** for unreliable networks
- **Concurrent processing** using multithreading
- **Image quality heuristics**, including:
  - Perceptual hashing (pHash) for duplicate detection
  - Color histogram similarity
  - Laplacian variance for blur detection
  - Dominant color analysis for placeholder / grey images
  - Aspect ratio and size validation
- **Checkpointing & resumability** to recover from partial failures
- **Defensive error handling** for unreadable or malformed images

---

## 🛠 Engineering Highlights

- Designed for **large-scale URL processing**
- Prioritizes **reliability over raw speed**
- Handles partial failures gracefully
- Modular logic that can be adapted to different data sources or platforms

---

## 📁 Repository Contents
appgit.py # Core pipeline logic and image analysis heuristics


## ⚠️ Important Note

> This repository is intended to showcase **engineering patterns and image-quality analysis techniques**.
>
> It is **not designed to run out-of-the-box** without adapting:
> - Data sources
> - Environment variables
> - Credentials or infrastructure

All company-specific data, credentials, and internal systems have been intentionally excluded.

---

## 🚀 Use Cases

- E-commerce image quality validation
- Catalog health monitoring
- Duplicate asset detection
- Content quality auditing pipelines

---

## 👤 Author

Mahesh Bathija
