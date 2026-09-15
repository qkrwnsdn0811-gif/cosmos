import hudson.model.Node
import hudson.security.FullControlOnceLoggedInAuthorizationStrategy
import hudson.security.HudsonPrivateSecurityRealm
import hudson.security.csrf.DefaultCrumbIssuer
import jenkins.model.Jenkins
import jenkins.model.JenkinsLocationConfiguration

def jenkins = Jenkins.get()
def realm = new HudsonPrivateSecurityRealm(false)
def authorization = new FullControlOnceLoggedInAuthorizationStrategy()
authorization.setAllowAnonymousRead(false)

// Save the access boundary before reading secrets or configuring plugins.
jenkins.setSecurityRealm(realm)
jenkins.setAuthorizationStrategy(authorization)
jenkins.setNumExecutors(0)
jenkins.setMode(Node.Mode.EXCLUSIVE)
jenkins.setSlaveAgentPort(-1)
jenkins.setCrumbIssuer(new DefaultCrumbIssuer(false))
jenkins.save()

def readSecret = { String name ->
    def file = new File('/run/secrets', name)
    if (!file.isFile() || !file.canRead()) {
        throw new IllegalStateException("Required secret is unreadable: ${name}")
    }
    def value = file.getText('UTF-8').trim()
    if (!value) {
        throw new IllegalStateException("Required secret is empty: ${name}")
    }
    value
}

def adminUser = readSecret('admin-user')
def adminPassword = readSecret('admin-password')
if (!(adminUser ==~ /[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}/) || adminPassword.length() < 8) {
    throw new IllegalStateException('Admin ID or password does not satisfy the bootstrap requirements')
}
realm.createAccount(adminUser, adminPassword)
jenkins.save()

def location = JenkinsLocationConfiguration.get()
location.setUrl(System.getenv('JENKINS_URL') ?: 'https://j15c205.p.ssafy.io/jenkins/')
location.save()
System.setProperty('cosmos.security.ready', 'true')
println('COSMOS Jenkins authentication configured; anonymous access and self-registration are disabled.')
