FROM jenkins/jenkins:2.568.3-jdk21

COPY --chown=jenkins:jenkins plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt

COPY --chown=jenkins:jenkins init.groovy.d/ /usr/share/jenkins/ref/init.groovy.d/
COPY --chown=jenkins:jenkins --chmod=755 cosmos-entrypoint.sh /usr/local/bin/cosmos-entrypoint.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/cosmos-entrypoint.sh"]
