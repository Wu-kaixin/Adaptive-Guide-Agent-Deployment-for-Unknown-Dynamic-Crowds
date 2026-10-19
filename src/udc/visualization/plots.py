"""Plot the scene, observed crowd, planned boundary and guide trajectories."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
import numpy as np


def plot_result(env, trajectory, controller, metrics, path):
    fig, ax = plt.subplots(figsize=(10, 6), constrained_layout=True)
    ax.plot(*np.asarray(env.scene.polygon.exterior.coords).T, color='#24344d', label='Room')
    crowd = env.observe()
    for point, radius in zip(crowd.positions, crowd.radii):
        ax.add_patch(Circle(point, radius, color='#dc8e55', alpha=.3))
    points = ax.scatter(*crowd.positions.T, c=crowd.demand, cmap='YlOrRd', s=16, label='Static crowd')
    fig.colorbar(points, ax=ax, label='Demand weight', shrink=.6)
    if controller.boundary is not None:
        for curve, label, color in [(controller.boundary.crowd, 'Estimated boundary', '#8b6c56'),
                                    (controller.boundary.deployment, 'Deployment curve', '#159a8c')]:
            ax.plot(*np.vstack([curve, curve[0]]).T, '--', color=color, label=label)
    for i in range(trajectory.shape[1]):
        ax.plot(*trajectory[:, i].T, color='#3778bf', alpha=.4, linewidth=.8)
    ax.scatter(*trajectory[0].T, facecolors='none', edgecolors='#3778bf', label='Initial guides')
    ax.scatter(*trajectory[-1].T, marker='^', color='#3778bf', label='Final guides')
    if controller.plan is not None:
        ax.scatter(*controller.plan.positions.T, marker='+', color='#159a8c', label='Targets')
    ax.set(aspect='equal', xlabel='x [m]', ylabel='y [m]', title=f"{env.scene.name} | {metrics['termination_status']}")
    ax.legend(loc='upper left', bbox_to_anchor=(1.02, 1), fontsize=8)
    ax.grid(alpha=.15)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_saved_result(result_dir, path=None):
    """Regenerate a representative scene solely from persisted evidence."""
    result_dir=Path(result_dir); data=np.load(result_dir/'trajectory.npz',allow_pickle=False)
    metrics=json.loads((result_dir/'metrics.json').read_text(encoding='utf-8'))
    room=data['room']; trajectory=data['positions']
    if room.shape!=(2,) or trajectory.ndim!=3 or not len(trajectory):
        raise ValueError('Saved result has no plottable execution trajectory')
    fig,ax=plt.subplots(figsize=(10,6),constrained_layout=True)
    rectangle=np.array([[0,0],[room[0],0],room,[0,room[1]],[0,0]])
    ax.plot(*rectangle.T,color='#24344d',label='Room')
    points=data['crowd_positions']; radii=data['crowd_radii']; demand=data['crowd_demand']
    for point,radius in zip(points,radii):
        ax.add_patch(Circle(point,radius,color='#dc8e55',alpha=.3))
    scatter=ax.scatter(*points.T,c=demand,cmap='YlOrRd',s=16,label='Static crowd')
    fig.colorbar(scatter,ax=ax,label='Demand weight',shrink=.6)
    for key,label,color in [('boundary_crowd','Estimated boundary','#8b6c56'),
                            ('boundary_deployment','Deployment curve','#159a8c')]:
        curve=data[key]
        if len(curve): ax.plot(*np.vstack([curve,curve[0]]).T,'--',color=color,label=label)
    for guide in range(trajectory.shape[1]):
        ax.plot(*trajectory[:,guide].T,color='#3778bf',alpha=.4,linewidth=.8)
    ax.scatter(*trajectory[0].T,facecolors='none',edgecolors='#3778bf',label='Initial guides')
    ax.scatter(*trajectory[-1].T,marker='^',color='#3778bf',label='Final guides')
    if len(data['targets']):
        ax.scatter(*data['targets'].T,marker='+',color='#159a8c',label='Targets')
    ax.set(aspect='equal',xlabel='x [m]',ylabel='y [m]',
           title=f"{metrics['scene']} | {metrics['method']} | {metrics['termination_status']}")
    ax.legend(loc='upper left',bbox_to_anchor=(1.02,1),fontsize=8); ax.grid(alpha=.15)
    destination=Path(path) if path else result_dir/'scene_from_saved.png'
    fig.savefig(destination,dpi=150); plt.close(fig)
    return destination


def plot_demand_profile(result_dir, path=None):
    """People, boundary demand curve and planned vs actual deployment from saved evidence.

    Panel A: crowd coloured by frozen demand, declared hotspot disk, boundary,
    targets and final guides. Panel B: the two curve densities that planners
    consume, with target and final arc positions. Panel C: consecutive gaps
    against the planned and acceptance limits.
    """
    import yaml
    from udc.boundary import Boundary
    from udc.deployment import demand_profile, periodic_gaps, quadrature_mass
    from udc.evaluation.coverage import arc_positions, hotspot_window
    result_dir=Path(result_dir); data=np.load(result_dir/'trajectory.npz',allow_pickle=False)
    metrics=json.loads((result_dir/'metrics.json').read_text(encoding='utf-8'))
    config=yaml.safe_load((result_dir/'config.yaml').read_text(encoding='utf-8'))
    if config is None or not len(data['boundary_deployment']):
        raise ValueError('Saved result has no boundary to plot')
    control=config['controller']; room=data['room']
    boundary=Boundary(data['boundary_crowd'],data['boundary_deployment'],data['boundary_arc'],float(data['boundary_length']))
    people=data['crowd_positions']; demand=data['crowd_demand']
    mask=data['crowd_hotspot_mask'] if 'crowd_hotspot_mask' in data.files and len(data['crowd_hotspot_mask']) else None
    mass_rho=demand_profile(boundary,people,demand,control['demand_bandwidth'],'mass_cvt')
    mean_rho=demand_profile(boundary,people,demand,control['demand_bandwidth'],'average_cvt')
    quadrature,_,length=quadrature_mass(boundary,mass_rho,control['quadrature_samples'])
    window=None if mask is None else hotspot_window(boundary,quadrature,people,mask)
    targets=data['targets']; trajectory=data['positions']; active=data['active_mask']
    final=trajectory[-1][active] if len(trajectory) and active.any() else np.empty((0,2))
    planned_arcs=np.sort(arc_positions(boundary,targets)) if len(targets) else np.empty(0)
    actual_arcs=np.sort(arc_positions(boundary,final)) if len(final) else np.empty(0)
    fig,axes=plt.subplots(1,3,figsize=(18,5.5),constrained_layout=True,gridspec_kw={'width_ratios':[1.15,1,1]})
    ax=axes[0]
    ax.plot(*np.array([[0,0],[room[0],0],room,[0,room[1]],[0,0]]).T,color='#24344d')
    scatter=ax.scatter(*people.T,c=demand,cmap='YlOrRd',s=22,edgecolors='#555',linewidths=.3,label='People (demand)')
    fig.colorbar(scatter,ax=ax,label='Demand weight',shrink=.7)
    region=(config.get('crowd') or {}).get('demand_region')
    if region:
        ax.add_patch(Circle(region['center'],region['radius'],fill=False,color='#c0392b',linestyle=':',linewidth=1.5,label='Declared hotspot disk'))
    for key,label,color in [('boundary_crowd','Estimated boundary','#8b6c56'),('boundary_deployment','Deployment curve','#159a8c')]:
        curve=data[key]; ax.plot(*np.vstack([curve,curve[0]]).T,'--',color=color,linewidth=1,label=label)
    if window is not None:
        curve=np.column_stack([np.interp(quadrature,np.r_[boundary.arc,length],np.r_[boundary.deployment[:,d],boundary.deployment[0,d]]) for d in range(2)])
        ax.plot(*curve[window].T,'.',color='#c0392b',markersize=2,label='Hotspot arc window')
    if len(targets): ax.scatter(*targets.T,marker='+',color='#159a8c',s=60,label='Planned targets')
    if len(final): ax.scatter(*final.T,marker='^',color='#3778bf',s=30,label='Final guides')
    ax.set(aspect='equal',xlabel='x [m]',ylabel='y [m]',title=f"{metrics['method']} | N={metrics.get('fixed_n') or metrics['active_guides']} | {metrics['termination_status']}")
    ax.legend(fontsize=7,loc='upper left'); ax.grid(alpha=.15)
    ax=axes[1]
    ax.plot(boundary.arc,mass_rho,color='#c0392b',label='Mass density (Mass CVT / RA-ABDC)')
    ax.plot(boundary.arc,mean_rho/mean_rho.max(),color='#8e44ad',alpha=.8,label='Average density / max (Average CVT)')
    if window is not None:
        ax.fill_between(quadrature,0,1.05,where=window,color='#c0392b',alpha=.08,label='Hotspot window')
    ax.vlines(planned_arcs,1.0,1.08,color='#159a8c',linewidth=1,label='Planned target arcs')
    ax.vlines(actual_arcs,-0.08,0.0,color='#3778bf',linewidth=1,label='Final guide arcs')
    ax.set(xlabel='arc length s [m]',ylabel='normalised curve density',xlim=(0,length),ylim=(-0.1,1.12),title='Boundary demand curve')
    ax.legend(fontsize=7,loc='lower left'); ax.grid(alpha=.15)
    ax=axes[2]
    planned_limit=control['max_gap_limit']-2*control['arc_tolerance']
    for arcs,label,color,marker in [(planned_arcs,'Planned gaps','#159a8c','+'),(actual_arcs,'Actual gaps','#3778bf','^')]:
        if len(arcs)>1:
            gaps=periodic_gaps(arcs,length); mid=(arcs+gaps/2)%length
            order=np.argsort(mid); ax.plot(mid[order],gaps[order],marker=marker,color=color,linewidth=.8,label=label)
    ax.axhline(planned_limit,color='#159a8c',linestyle='--',linewidth=1,label=f'Planned limit {planned_limit:.2f} m')
    ax.axhline(control['max_gap_limit'],color='#c0392b',linestyle='--',linewidth=1,label=f"Acceptance limit {control['max_gap_limit']:.2f} m")
    if window is not None:
        ax.fill_between(quadrature,0,control['max_gap_limit']*1.1,where=window,color='#c0392b',alpha=.08)
    ax.set(xlabel='gap midpoint arc [m]',ylabel='consecutive gap [m]',xlim=(0,length),ylim=(0,control['max_gap_limit']*1.1),title='Gaps along the curve')
    ax.legend(fontsize=7,loc='lower left'); ax.grid(alpha=.15)
    fig.suptitle(f"{metrics['scene']} seed {metrics['seed']} | demand: {metrics.get('demand_kind')} | planned conc. {metrics.get('planned_hotspot_concentration')} | actual conc. {metrics.get('actual_hotspot_concentration')}",fontsize=10)
    destination=Path(path) if path else result_dir/'demand_profile.png'
    fig.savefig(destination,dpi=150); plt.close(fig)
    return destination


def plot_comparison_summary(summary_path, path=None):
    """Grouped bars per cell and method from comparison_summary.json (all tasks in denominators)."""
    summary_path=Path(summary_path)
    summary=json.loads(summary_path.read_text(encoding='utf-8'))
    groups=list(summary['groups'].values())
    cells=sorted({g['cell'] for g in groups}); methods=sorted({g['method'] for g in groups})
    lookup={(g['cell'],g['method']):g for g in groups}
    panels=[('Success rate (all tasks)',lambda g:g['success_rate']),
            ('Actual max gap [m], mean over defined',lambda g:g['metrics']['actual_max_gap']['mean']),
            ('Actual demand-weighted cost [m^2]',lambda g:g['metrics']['actual_demand_weighted_cost']['mean']),
            ('Actual hotspot concentration (1 = unbiased)',lambda g:g['metrics']['actual_hotspot_concentration']['mean'])]
    fig,axes=plt.subplots(2,2,figsize=(16,9),constrained_layout=True)
    width=0.8/len(methods); x=np.arange(len(cells))
    colors=plt.get_cmap('tab10')
    for ax,(title,getter) in zip(axes.ravel(),panels):
        for k,method in enumerate(methods):
            values=[]
            for cell in cells:
                g=lookup.get((cell,method)); value=None if g is None else getter(g)
                values.append(np.nan if value is None else value)
            ax.bar(x+(k-len(methods)/2+.5)*width,values,width,label=method,color=colors(k))
        labels=[cell.replace('square_','').replace('_medium','') for cell in cells]
        ax.set(title=title,xticks=x,xticklabels=labels)
        ax.tick_params(axis='x',labelsize=7,labelrotation=60); ax.grid(axis='y',alpha=.2)
    axes[0,0].legend(fontsize=8); axes[0,0].set_ylim(0,1.05)
    fig.suptitle(f"Step 1 comparison: {summary['tasks']} tasks, reference {summary.get('reference_method')}",fontsize=11)
    destination=Path(path) if path else summary_path.with_name('comparison_summary.png')
    fig.savefig(destination,dpi=150); plt.close(fig)
    return destination


def plot_seed_deltas(report, path):
    """Seed-level mean cost difference of projected_mass minus Equal Arc. Lower is better."""
    paired = report['paired']['projected_mass']['planned_demand_weighted_cost']
    seeds = list(paired['seed_means'])
    values = [paired['seed_means'][seed] for seed in seeds]
    fig, ax = plt.subplots(figsize=(8, 4), constrained_layout=True)
    ax.bar(range(len(seeds)), values, color='#1d4e89')
    ax.axhline(0.0, color='#333', lw=0.8)
    cluster = paired['seed_cluster']
    if cluster['ci95'] is not None:
        ax.axhline(cluster['mean'], color='#c0392b', lw=1, label=f"seed mean {cluster['mean']:.5f}")
    ax.set_xticks(range(len(seeds)), [str(seed) for seed in seeds])
    ax.set(xlabel='seed', ylabel='planned demand-weighted cost difference [m$^2$]')
    ax.set_title('projected_mass minus Equal Arc, mean within each seed')
    ax.legend(fontsize=8)
    ax.grid(axis='y', alpha=0.2)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=150)
    plt.close(fig)
    return destination


def _seed_bar(ax, seed_means, ylabel, title):
    seeds = list(seed_means)
    values = [seed_means[seed] for seed in seeds]
    ax.bar(range(len(seeds)), values, color='#1d4e89')
    ax.axhline(0.0, color='#333', lw=0.8)
    ax.set_xticks(range(len(seeds)), [str(seed) for seed in seeds], fontsize=7, rotation=60)
    ax.set(ylabel=ylabel, title=title)
    ax.grid(axis='y', alpha=0.2)


def plot_formal_summary(report, destination):
    """Four frozen formal figures. Panels are not chosen from the outcome."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    overall = report['overall']
    paired = overall['paired']['projected_mass']
    methods = ['equal_arc', 'mass_cvt', 'projected_mass']
    stage_names = ['accepted', 'no_plan_found', 'certified_infeasible',
                   'plan_found_assignment_failed', 'plan_found_execution_failed']
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    rates = [overall['methods'][name]['successful'] / overall['methods'][name]['tasks'] for name in methods]
    axes[0].bar(methods, rates, color=['#8aa0b8', '#c47b4a', '#1d4e89'])
    axes[0].set_ylim(0, 1.05)
    axes[0].set(ylabel='Success rate', title='Full-task success')
    axes[0].grid(axis='y', alpha=0.2)
    bottoms = [0, 0, 0]
    colors = ['#1d4e89', '#c47b4a', '#8a3040', '#5c4b8a', '#2f6f4e']
    for stage, color in zip(stage_names, colors):
        counts = [overall['methods'][name]['stages'].get(stage, 0) for name in methods]
        axes[1].bar(methods, counts, bottom=bottoms, color=color, label=stage)
        bottoms = [base + count for base, count in zip(bottoms, counts)]
    axes[1].set(ylabel='Tasks', title='Failure stage')
    axes[1].legend(fontsize=7)
    axes[1].grid(axis='y', alpha=0.2)
    fig.savefig(destination / 'success_stages.png', dpi=150)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(10, 4), constrained_layout=True)
    _seed_bar(ax, paired['planned_demand_weighted_cost']['seed_means'],
              'planned cost difference [m$^2$]', 'projected_mass minus Equal Arc')
    fig.savefig(destination / 'demand_cost_deltas.png', dpi=150)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    _seed_bar(axes[0], paired['planned_excess_weighted_cost']['seed_means'],
              'excess cost difference [m$^2$]', 'Hotspot excess')
    _seed_bar(axes[1], paired['planned_background_weighted_cost']['seed_means'],
              'background cost difference [m$^2$]', 'Background arc')
    fig.savefig(destination / 'hotspot_background.png', dpi=150)
    plt.close(fig)
    fig, axes = plt.subplots(1, 3, figsize=(12, 4), constrained_layout=True)
    _seed_bar(axes[0], paired['path_length']['seed_means'], 'path difference [m]', 'Path length')
    _seed_bar(axes[1], paired['simulation_time']['seed_means'], 'time difference [s]', 'Deployment time')
    _seed_bar(axes[2], paired['planner_seconds']['seed_means'], 'planner difference [s]', 'Planning time')
    fig.savefig(destination / 'cost_of_deployment.png', dpi=150)
    plt.close(fig)
    return destination


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--result',help='Saved task directory')
    group.add_argument('--comparison-summary',help='comparison_summary.json from udc.experiments.comparison')
    parser.add_argument('--demand-profile',action='store_true',help='Plot the demand curve and gaps instead of the scene')
    parser.add_argument('--output')
    args=parser.parse_args()
    if args.comparison_summary:
        print(plot_comparison_summary(args.comparison_summary,args.output)); return
    print(plot_demand_profile(args.result,args.output) if args.demand_profile else plot_saved_result(args.result,args.output))


if __name__=='__main__':
    main()
