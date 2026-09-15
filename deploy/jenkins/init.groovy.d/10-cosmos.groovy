import com.cloudbees.plugins.credentials.CredentialsScope
import com.cloudbees.plugins.credentials.SystemCredentialsProvider
import com.cloudbees.plugins.credentials.impl.UsernamePasswordCredentialsImpl
import com.dabsquared.gitlabjenkins.GitLabPushTrigger
import com.dabsquared.gitlabjenkins.trigger.TriggerOpenMergeRequest
import com.dabsquared.gitlabjenkins.trigger.filter.BranchFilterType
import hudson.model.Node
import hudson.plugins.git.BranchSpec
import hudson.plugins.git.GitSCM
import hudson.plugins.git.UserRemoteConfig
import hudson.plugins.git.extensions.impl.CloneOption
import hudson.slaves.DumbSlave
import hudson.slaves.JNLPLauncher
import hudson.slaves.RetentionStrategy
import hudson.triggers.SCMTrigger
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermissions
import jenkins.model.BuildDiscarderProperty
import jenkins.model.Jenkins
import hudson.tasks.LogRotator
import org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition
import org.jenkinsci.plugins.workflow.job.WorkflowJob
import org.jenkinsci.plugins.workflow.job.properties.DisableConcurrentBuildsJobProperty
import org.jenkinsci.plugins.workflow.job.properties.PipelineTriggersJobProperty

if (System.getProperty('cosmos.security.ready') != 'true') {
    throw new IllegalStateException('COSMOS security initialization did not complete; refusing to create jobs')
}

def jenkins = Jenkins.get()
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

def webhookToken = readSecret('webhook-token')
if (webhookToken.length() < 32) {
    throw new IllegalStateException('Webhook token must contain at least 32 characters')
}

def credentialId = 'cosmos-git-read'
def hasGitUserFile = new File('/run/secrets/git-username').exists()
def hasGitPasswordFile = new File('/run/secrets/git-password').exists()
if (hasGitUserFile != hasGitPasswordFile) {
    throw new IllegalStateException('Optional Git credential files must be supplied as a pair')
}
if (hasGitUserFile) {
    def gitUser = readSecret('git-username')
    def gitPassword = readSecret('git-password')
    def provider = SystemCredentialsProvider.getInstance()
    def credentials = provider.getCredentials()
    def existingCredential = credentials.find { it.id == credentialId }
    def credential = new UsernamePasswordCredentialsImpl(
        CredentialsScope.GLOBAL, credentialId, 'COSMOS GitLab repository read access', gitUser, gitPassword
    )
    if (existingCredential) {
        credentials.set(credentials.indexOf(existingCredential), credential)
    } else {
        credentials.add(credential)
    }
    provider.save()
} else {
    // Leave a UI-created credential untouched. Missing credentials are allowed
    // while the secured controller waits for the project Deploy Token owner.
    println('COSMOS repository credentials are managed in the Jenkins UI; no credential was created or changed.')
}

def launcher = new JNLPLauncher()
launcher.setWebSocket(true)
def agent = jenkins.getNode('cosmos-agent')
if (agent != null && !(agent instanceof DumbSlave)) {
    throw new IllegalStateException('cosmos-agent already exists with an incompatible node type')
}
if (agent == null) {
    agent = new DumbSlave('cosmos-agent', '/opt/cosmos/jenkins-agent', launcher)
} else if (agent.getRemoteFS() != '/opt/cosmos/jenkins-agent') {
    throw new IllegalStateException('Existing cosmos-agent has an unexpected remote root directory')
}
agent.setNodeDescription('Dedicated EC2 host agent; Docker builds execute here, never on the controller')
agent.setNumExecutors(1)
agent.setLabelString('cosmos-build')
agent.setMode(Node.Mode.EXCLUSIVE)
agent.setLauncher(launcher)
agent.setRetentionStrategy(new RetentionStrategy.Always())
jenkins.addNode(agent)

// The bootstrap copies this protected file directly to the host agent secret file.
// Never print the agent secret or send it to a build workspace.
def secretPath = new File(jenkins.rootDir, 'cosmos-agent.secret').toPath()
if (!Files.exists(secretPath)) {
    Files.createFile(secretPath, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString('rw-------')))
}
Files.setPosixFilePermissions(secretPath, PosixFilePermissions.fromString('rw-------'))
Files.writeString(secretPath, agent.toComputer().getJnlpMac() + '\n')

def repository = 'https://lab.ssafy.com/s15-bigdata-dist-sub1/S15P21C205.git'
def configureJob = { String name, String branch, List triggers ->
    def job = jenkins.getItem(name)
    if (job != null && !(job instanceof WorkflowJob)) {
        throw new IllegalStateException("Existing item is not a Pipeline job: ${name}")
    }
    if (job != null) {
        println("Preserving existing Pipeline configuration: ${name}")
        return
    }
    job = jenkins.createProject(WorkflowJob, name)
    def clone = new CloneOption(false, true, null, 10)
    clone.setHonorRefspec(true)
    def scm = new GitSCM(
        [new UserRemoteConfig(repository, 'origin', "+refs/heads/${branch}:refs/remotes/origin/${branch}", credentialId)],
        [new BranchSpec("*/${branch}")],
        false, [], null, null, [clone]
    )
    def definition = new CpsScmFlowDefinition(scm, 'Jenkinsfile')
    definition.setLightweight(true)
    job.setDefinition(definition)
    job.setDescription("COSMOS Pipeline. Source branch: ${branch}. Agent: cosmos-build. Repository credential: cosmos-git-read (configure in Jenkins Credentials before the first build).")
    job.setQuietPeriod(5)
    job.addProperty(new BuildDiscarderProperty(new LogRotator(30, 30, 7, 5)))
    job.addProperty(new DisableConcurrentBuildsJobProperty())
    job.removeProperty(PipelineTriggersJobProperty)
    job.addProperty(new PipelineTriggersJobProperty(triggers))
    job.save()
    // Deliberately do not schedule a build here.
}

def push = new GitLabPushTrigger()
push.setTriggerOnPush(true)
push.setTriggerToBranchDeleteRequest(false)
push.setTriggerOnMergeRequest(false)
push.setTriggerOnAcceptedMergeRequest(false)
push.setTriggerOnApprovedMergeRequest(false)
push.setTriggerOnClosedMergeRequest(false)
push.setTriggerOnNoteRequest(false)
push.setTriggerOnPipelineEvent(false)
push.setTriggerOpenMergeRequestOnPush(TriggerOpenMergeRequest.never)
push.setBranchFilterType(BranchFilterType.NameBasedFilter)
push.setIncludeBranchesSpec('dev')
push.setExcludeBranchesSpec('')
push.setSecretToken(webhookToken)
push.setCiSkip(true)
push.setSetBuildDescription(false)
def triggers = [push]
if (System.getenv('COSMOS_ENABLE_POLLING') == 'true') {
    triggers.add(new SCMTrigger('H/5 * * * *'))
}
configureJob('cosmos-web', 'dev', triggers)
configureJob('cosmos-ci-116', 'chore/S15P21C205-116-jenkins-ec2-cicd', [])

jenkins.save()
new File(jenkins.rootDir, 'cosmos-init.ready').setText('ready\n', 'UTF-8')
println('COSMOS Jenkins jobs and WebSocket agent configured. No build was scheduled by initialization.')
