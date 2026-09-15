pipeline {
    agent { label 'cosmos-build' }
    options {
        skipDefaultCheckout(true)
        disableConcurrentBuilds()
        timeout(time: 45, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '20'))
        timestamps()
    }
    stages {
        stage('Checkout') {
            steps {
                deleteDir()
                checkout scm
                script {
                    env.IMAGE_TAG = sh(script: 'git rev-parse --short=12 HEAD', returnStdout: true).trim() + '-' + env.BUILD_NUMBER
                }
            }
        }
        stage('Test and build') {
            steps { sh 'bash deploy/scripts/ci.sh "$IMAGE_TAG"' }
            post { always { junit testResults: 'deploy/reports/backend/*.xml', allowEmptyResults: true } }
        }
        stage('Package images') {
            steps { sh 'bash deploy/scripts/build-images.sh "$IMAGE_TAG"' }
        }
        stage('Deploy dev') {
            when {
                expression { env.JOB_BASE_NAME == 'cosmos-web' && (env.GIT_BRANCH == 'origin/dev' || env.GIT_BRANCH == 'dev') }
            }
            steps {
                // A newer dev commit must not be overwritten by an older queued build.
                withCredentials([gitUsernamePassword(credentialsId: 'cosmos-git-read', gitToolName: 'Default')]) {
                    sh '''
                        git fetch --no-tags origin dev
                        test "$(git rev-parse HEAD)" = "$(git rev-parse origin/dev)"
                    '''
                }
                sh 'COSMOS_SMOKE_URL=https://j15c205.p.ssafy.io bash deploy/scripts/deploy.sh "$IMAGE_TAG"'
            }
        }
    }
}
