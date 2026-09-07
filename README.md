# Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds

##Background:
In disaster response, large events, and public safety incidents, uncontrolled crowd dispersion may cause serious risks, such as people entering dangerous areas, blocking rescue routes, or causing stampedes. Therefore, it is important to quickly form temporary safety boundaries around crowds. This study aims to enable multiple guide agents to autonomously find unknown crowds and adaptively distribute themselves around them according to crowd size, shape, and motion, so that dynamic crowd containment can be achieved.

Problem statement:
We consider an unknown and dynamic crowd environment. The location, number, shape, size, and motion of crowds are unknown, and crowds may move, disperse, split, or merge over time. Multiple guide agents are deployed with limited sensing and communication ranges. The control input is the velocity of each guide agent, and the output is the spatial distribution of the guide agents around the crowds. The goal is to let the guide agents autonomously search for crowds and adaptively form containment distributions. Larger or higher-risk crowds should receive more agents, while smaller or stable crowds should receive fewer agents.

Research plan:
This research will be conducted in three steps.

Step 1: Single static crowd, no crowd-guide interaction, no communication limitation.
Develop a basic guide distribution control method for one static crowd, where guide agents can freely communicate and do not affect crowd dynamics.

Step 2: Multiple dynamic crowds, with crowd-guide interaction, no communication limitation.
Extend the problem to multiple dynamic crowds that may move, disperse, split, or merge. Study adaptive allocation and containment when guide agents can affect crowd behavior but have no communication limitation.

Step 3: Multiple dynamic crowds, with crowd-guide interaction, with communication limitation.
Develop a decentralized algorithm under limited communication, so that each guide agent can use only local sensing and local communication to search for crowds, identify crowd groups, allocate agents, and achieve dynamic containment.
