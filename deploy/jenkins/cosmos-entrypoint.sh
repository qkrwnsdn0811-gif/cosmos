#!/usr/bin/env bash
set -euo pipefail
umask 077

# Reject incomplete provisioning before Jenkins can open an HTTP listener.
for name in admin-user admin-password webhook-token; do
  if [[ ! -r "/run/secrets/${name}" || ! -s "/run/secrets/${name}" ]]; then
    printf 'Required Jenkins secret is missing or unreadable: %s\n' "$name" >&2
    exit 1
  fi
done

# Repository credentials normally come from the Jenkins UI. Optional file-based
# provisioning requires a complete pair; never start with half a credential.
if [[ -e /run/secrets/git-username || -e /run/secrets/git-password ]]; then
  for name in git-username git-password; do
    if [[ ! -r "/run/secrets/${name}" || ! -s "/run/secrets/${name}" ]]; then
      printf 'Optional Git credential files must be supplied as a readable, nonempty pair.\n' >&2
      exit 1
    fi
  done
fi

rm -f -- "${JENKINS_HOME}/cosmos-init.ready"

# Jenkins' reference-copy mechanism skips files already present in a volume.
# These two managed hooks must follow the image on every restart/upgrade.
install -d -m 700 "${JENKINS_HOME}/init.groovy.d"
for name in 00-security.groovy 10-cosmos.groovy; do
  install -m 600 "/usr/share/jenkins/ref/init.groovy.d/${name}" \
    "${JENKINS_HOME}/init.groovy.d/${name}"
done

# A first boot is authenticated even if a later Groovy script fails to compile.
if [[ ! -e "${JENKINS_HOME}/config.xml" ]]; then
  cat > "${JENKINS_HOME}/config.xml" <<'XML'
<?xml version='1.1' encoding='UTF-8'?>
<hudson>
  <numExecutors>0</numExecutors>
  <mode>EXCLUSIVE</mode>
  <useSecurity>true</useSecurity>
  <authorizationStrategy class="hudson.security.FullControlOnceLoggedInAuthorizationStrategy">
    <denyAnonymousReadAccess>true</denyAnonymousReadAccess>
  </authorizationStrategy>
  <securityRealm class="hudson.security.HudsonPrivateSecurityRealm">
    <disableSignup>true</disableSignup>
    <enableCaptcha>false</enableCaptcha>
  </securityRealm>
  <slaveAgentPort>-1</slaveAgentPort>
  <crumbIssuer class="hudson.security.csrf.DefaultCrumbIssuer">
    <excludeClientIPFromCrumb>false</excludeClientIPFromCrumb>
  </crumbIssuer>
</hudson>
XML
fi

exec /usr/local/bin/jenkins.sh "$@"
