# NZ Employment Law Assistant

An AI-powered chatbot for answering New Zealand employment law questions. Built with Retrieval-Augmented Generation (RAG) and Claude API.

## Overview

This application helps users understand their rights and obligations under New Zealand employment law. It uses:

- **RAG (Retrieval-Augmented Generation)** for accurate, source-backed answers
- **Claude API** for intelligent question answering
- **Streamlit** for a clean, user-friendly interface
- **Chroma** for vector database and semantic search

## Features

- ✅ Instant answers to NZ employment law questions
- ✅ Source citations from official government documents
- ✅ Clean, accessible UI with light/dark mode
- ✅ No data collection — all conversations are private
- ✅ Sidebar navigation with helpful information
- ✅ Modal dialogs for Privacy, Disclaimer, and Terms

## Running Locally

### Prerequisites
- Python 3.10+
- An Anthropic API key 


## Project Structure

```
.
├── app.py                    # Main Streamlit application
├── rag_query.py              # RAG querying logic
├── requirements.txt          # Python dependencies
├── vectorstore/              # Generated vectorstore (not in repo)
```
<img width="1800" height="1240" alt="product-flow" src="https://github.com/user-attachments/assets/924d2c3a-35da-4b60-b5a6-7e17ea568972" />

## Deployment

This application is deployed on Railway and accessible at:
**https://nzlaw.linkiwise.com**

### Environment Variables

When deploying, set these environment variables:

- `ANTHROPIC_API_KEY` — Your Anthropic API key (required)
- `STREAMLIT_SERVER_PORT` — Port for the server (default: 8501)

## Cost Estimate

Using Claude Haiku (most cost-effective):
- ~$0.015 per user query
- 100 queries/month ≈ $1.50
- 1,000 queries/month ≈ $15

## Legal Notice

⚖️ **This is not legal advice.** This tool provides general informational purposes only. For serious employment matters, consult a qualified employment lawyer.

All information is sourced from official New Zealand government websites (Employment New Zealand, MBIE, etc.).

## Privacy

No user data is collected. All conversations are temporary and exist only in your browser session. They are permanently deleted when you close or refresh the page.

## Support

For issues or questions:
- Email: laura01@linkiwise.com
- LinkedIn: https://www.linkedin.com/in/laurahfc/

## License

Built with ❤️ by Laura Cai | [Portfolio](https://linkiwise.com)
