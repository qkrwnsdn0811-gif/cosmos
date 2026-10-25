#!/usr/bin/env bash
# Creates the GCP side of the NEXUS demo: project, registry, dump bucket, VM, and the identity that
# GitHub Actions deploys with. Re-running it keeps existing resources and only adds what is missing.
# Run from a workstation where gcloud is signed in to an account that can create projects and link billing.
set -Eeuo pipefail

project="${PROJECT_ID:-cosmos-nexus-1026}"
billing_account="${BILLING_ACCOUNT:-01AE03-48E726-85FD06}"
region="${REGION:-asia-northeast3}"
zone="${ZONE:-asia-northeast3-a}"
github_repository="${GITHUB_REPOSITORY:-qkrwnsdn0811-gif/cosmos}"
deploy_branch="${DEPLOY_BRANCH:-main}"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1 CLOUDSDK_CORE_PROJECT="$project"

vm_sa="nexus-vm@$project.iam.gserviceaccount.com"
deployer_sa="github-deployer@$project.iam.gserviceaccount.com"
bucket="gs://$project-dumps"

exists() { "$@" > /dev/null 2>&1; }
# New service accounts take a few seconds before IAM bindings can name them, and a newly enabled
# API can refuse requests for about a minute.
retry() {
    local attempt
    for ((attempt = 1; attempt <= 6; attempt++)); do
        "$@" > /dev/null && return 0
        sleep 10
    done
    return 1
}

exists gcloud projects describe "$project" || gcloud projects create "$project" --name='COSMOS NEXUS demo'
[[ "$(gcloud billing projects describe "$project" --format='value(billingEnabled)')" == True ]] ||
    gcloud billing projects link "$project" --billing-account="$billing_account"
gcloud services enable compute.googleapis.com artifactregistry.googleapis.com iam.googleapis.com \
    iamcredentials.googleapis.com sts.googleapis.com iap.googleapis.com oslogin.googleapis.com \
    storage.googleapis.com cloudresourcemanager.googleapis.com
project_number="$(gcloud projects describe "$project" --format='value(projectNumber)')"

exists gcloud artifacts repositories describe cosmos --location="$region" ||
    retry gcloud artifacts repositories create cosmos --repository-format=docker --location="$region" \
        --description='NEXUS demo images (GitHub Actions)'
exists gcloud storage buckets describe "$bucket" ||
    gcloud storage buckets create "$bucket" --location="$region" \
        --uniform-bucket-level-access --public-access-prevention

exists gcloud iam service-accounts describe "$vm_sa" ||
    gcloud iam service-accounts create nexus-vm --display-name='NEXUS demo VM'
exists gcloud iam service-accounts describe "$deployer_sa" ||
    gcloud iam service-accounts create github-deployer --display-name='GitHub Actions deploy (NEXUS)'

retry gcloud artifacts repositories add-iam-policy-binding cosmos --location="$region" \
    --member="serviceAccount:$vm_sa" --role=roles/artifactregistry.reader
retry gcloud storage buckets add-iam-policy-binding "$bucket" \
    --member="serviceAccount:$vm_sa" --role=roles/storage.objectViewer
retry gcloud artifacts repositories add-iam-policy-binding cosmos --location="$region" \
    --member="serviceAccount:$deployer_sa" --role=roles/artifactregistry.writer
for role in roles/compute.viewer roles/compute.osAdminLogin roles/iap.tunnelResourceAccessor; do
    retry gcloud projects add-iam-policy-binding "$project" --condition=None \
        --member="serviceAccount:$deployer_sa" --role="$role"
done
retry gcloud iam service-accounts add-iam-policy-binding "$vm_sa" \
    --member="serviceAccount:$deployer_sa" --role=roles/iam.serviceAccountUser

# Only this repository's deploy branch may exchange a GitHub OIDC token for the deployer.
# The condition is reapplied on every run so a changed DEPLOY_BRANCH reaches an existing provider.
wif_condition="assertion.repository == '$github_repository' && assertion.ref == 'refs/heads/$deploy_branch'"
exists gcloud iam workload-identity-pools describe github --location=global ||
    gcloud iam workload-identity-pools create github --location=global --display-name='GitHub Actions'
if exists gcloud iam workload-identity-pools providers describe cosmos-nexus --workload-identity-pool=github --location=global; then
    gcloud iam workload-identity-pools providers update-oidc cosmos-nexus --workload-identity-pool=github \
        --location=global --attribute-condition="$wif_condition"
else
    gcloud iam workload-identity-pools providers create-oidc cosmos-nexus --workload-identity-pool=github \
        --location=global --issuer-uri=https://token.actions.githubusercontent.com \
        --attribute-mapping='google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref' \
        --attribute-condition="$wif_condition"
fi
retry gcloud iam service-accounts add-iam-policy-binding "$deployer_sa" --role=roles/iam.workloadIdentityUser \
    --member="principalSet://iam.googleapis.com/projects/$project_number/locations/global/workloadIdentityPools/github/attribute.repository/$github_repository"

# The web ports are public; SSH is reachable only through IAP.
exists gcloud compute firewall-rules describe cosmos-web ||
    gcloud compute firewall-rules create cosmos-web --network=default --direction=INGRESS \
        --allow=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 --target-tags=cosmos-web
exists gcloud compute firewall-rules describe cosmos-iap-ssh ||
    gcloud compute firewall-rules create cosmos-iap-ssh --network=default --direction=INGRESS \
        --allow=tcp:22 --source-ranges=35.235.240.0/20 --target-tags=cosmos-web
for rule in default-allow-ssh default-allow-rdp; do
    ! exists gcloud compute firewall-rules describe "$rule" || gcloud compute firewall-rules delete "$rule"
done

# A reserved address keeps the sslip.io name, certificate, and CORS origin across VM restarts.
exists gcloud compute addresses describe nexus-ip --region="$region" ||
    gcloud compute addresses create nexus-ip --region="$region"
static_ip="$(gcloud compute addresses describe nexus-ip --region="$region" --format='value(address)')"
exists gcloud compute instances describe nexus-vm --zone="$zone" ||
    gcloud compute instances create nexus-vm --zone="$zone" --machine-type=e2-standard-2 \
        --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
        --boot-disk-size=30GB --boot-disk-type=pd-balanced --address="$static_ip" --tags=cosmos-web \
        --service-account="$vm_sa" --scopes=cloud-platform --metadata=enable-oslogin=TRUE \
        --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring --deletion-protection

printf 'PROJECT_ID=%s\n' "$project"
printf 'PROJECT_NUMBER=%s\n' "$project_number"
printf 'WIF_PROVIDER=projects/%s/locations/global/workloadIdentityPools/github/providers/cosmos-nexus\n' "$project_number"
printf 'DEPLOYER_SA=%s\n' "$deployer_sa"
printf 'REGISTRY=%s-docker.pkg.dev/%s/cosmos\n' "$region" "$project"
printf 'DUMP_BUCKET=%s\n' "$bucket"
printf 'STATIC_IP=%s\n' "$static_ip"
printf 'DEMO_URL=https://%s.sslip.io\n' "${static_ip//./-}"
