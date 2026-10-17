# Changelog

All notable changes to Local AI Monitoring are documented in this file.

The project has no release tags yet. Entries follow the dates of the changes on `main`. The format is based on [Keep a Changelog](https://keepachangelog.com/).

## 2026-10-18

### Changed

- Recent requests are ordered by start time, newest first. Errors no longer stay above newer finished requests.
- The Time, Status, Source, Model, Prompt, Output, tok/s, and Duration headers sort the list. Clicking the active header reverses the direction. The Question column is not sortable. Empty tok/s and duration values stay at the end, and equal values keep the newer request first.

## 2026-10-12

### Fixed

- On narrow screens the request table scrolls inside its card. The page itself no longer scrolls sideways.

## 2026-10-10

### Added

- Web dashboard for an AMD Strix Halo host running local models with llama-server. It shows which models are loaded, whether each one is idle, reading, or generating, how many requests are active or waiting, and the recent requests with question, model, token counts, speed, and duration.
- Live host charts for CPU, GPU load, GTT memory, temperature, power, and disk activity. On Strix Halo the VRAM figure is labeled as the small BIOS reserve, because the models live in shared GTT memory.
- Live requests from llama-server `/slots` and `/metrics`. Finished requests that went through LiteLLM are read from `LiteLLM_SpendLogs` and are not copied into a second store. Requests sent straight to llama-server are kept in a short local SQLite history.
- A llama-server in router mode is queried only for models that are already loaded. The dashboard does not load or wake a sleeping model.
- Hostname, IP address, operating system, and the LiteLLM and llama-server versions, read from the host rather than from the container image.
- English and German interface. The default comes from the configuration. Each visitor can switch, and the browser remembers the choice.
- Light and dark appearance, with the same remembered choice. Without one, the page follows the browser.
- Network Publishing logo, and a layout that uses the full window width so the questions have more room.
- Sample data for machines without a Strix Halo (`HALO_FIXTURES=1`), a rootless Podman Quadlet, and an nginx example that leaves buffering off so live updates arrive immediately.
- AGPL-3.0 license.

### Changed

- The README explains what the dashboard shows and how to set it up, and includes a screenshot of the interface.
