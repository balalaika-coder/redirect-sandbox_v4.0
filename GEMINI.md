# Project: Redirect Sandbox (v4.0)

A comprehensive Django-based system for managing URL redirects across multiple sites with advanced geo-aware routing capabilities.

## Project Overview
This project provides a centralized dashboard for managing complex redirect rules. It is designed to be the "source of truth" for redirects that are eventually published to edge locations (e.g., AWS CloudFront using Lambda@Edge or CloudFront Functions).

### Key Technologies
- **Backend:** Django 6.0.4, Django REST Framework 3.17.1
- **Database:** SQLite (development), PostgreSQL support (`psycopg2-binary`)
- **Infrastructure:** AWS CDK (skeleton initialized in `infra/`)
- **API:** RESTful endpoints for synchronization with edge services

### Core Features
- **Multi-Site Management:** Manage redirects for multiple domains from a single interface.
- **Redirect Types:** Supports Exact Match, Regular Expression, and Vanity URLs.
- **Geo-Aware Routing:** Map ISO country codes to locale paths (e.g., US -> `/en-us`, DE -> `/de`) with support for locale overrides and prefixing.
- **Publishing Workflow:** Webhook-based system to trigger synchronization with external infrastructure.
- **Import/Export:** Bulk management via CSV files.
- **RBAC:** Per-site Role-Based Access Control (Admin/Editor).
- **Audit Trail:** Detailed logging of all changes and publishing events.
- **URL Tester:** Integrated admin tool to simulate redirect resolution including geo-logic.

## Directory Structure
- `app/`: Django application source code.
  - `redirect_project/`: Project configuration (settings, urls, wsgi).
  - `redirects_django/`: Main app logic (models, admin, api).
  - `templates/admin/`: Custom admin templates for CSV import and URL testing.
- `infra/`: AWS CDK infrastructure code (Skeleton).
  - `lambdas/`: Placeholder for edge synchronization logic.
- `ansible/`: (Empty) Reserved for deployment automation.

## Building and Running

### Prerequisites
- Python 3.12+
- Virtual environment (recommended)

### Local Development Setup
1. **Navigate to the app directory:**
   ```bash
   cd app
   ```
2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Run migrations:**
   ```bash
   python manage.py migrate
   ```
4. **Create a superuser:**
   ```bash
   python manage.py createsuperuser
   ```
5. **Start the development server:**
   ```bash
   python manage.py runserver
   ```
6. **Access the Admin UI:** `http://127.0.0.1:8000/admin/`

### Infrastructure (CDK)
1. **Navigate to the infra directory:**
   ```bash
   cd infra
   ```
2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Synthesize CloudFormation templates:**
   ```bash
   cdk synth
   ```

## Development Conventions

### Models and Logic
- **Geo-aware Redirects:** Must use relative destination paths and are incompatible with Regex match types.
- **Match Priority:** Regex redirects use the `priority` field to determine evaluation order.
- **Validation:** Extensive validation is implemented in `Model.clean()` methods to ensure data integrity before saving.

### API
- API endpoints are located under `/api/v1/`.
- Authentication uses `TokenAuthentication`.
- Primary endpoint: `GET /api/v1/sites/<slug>/redirects/` returns the full redirect state for a site.

### Administrative Tools
- **Bulk Import:** Use the "Import CSV" button in the Redirect list view. Upsert behavior is supported based on site and source path.
- **Testing:** Use the "Test URL" tool in the Admin to verify how a path resolves for a specific site and country code.
- **Publishing:** The "Publish selected" action triggers a signed HMAC webhook to the URL configured on the Site.

## TODO / Roadmap
- [ ] Implement Lambda@Edge logic in `infra/lambdas/edge_redirect/`.
- [ ] Implement DynamoDB sync logic in `infra/lambdas/sync_dynamodb/`.
- [ ] Add unit and integration tests in `app/redirects_django/tests.py`.
- [ ] Configure Ansible playbooks for server deployment.
- [ ] Add support for CloudFront KeyValueStore (KVS) synchronization.
