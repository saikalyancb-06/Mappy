import { LocateFixed, MapPinned, Navigation, X } from 'lucide-react'
import { describeFreshness } from '../hooks/useDeviceLocation'

// Always shows the two separate pieces of geographic state: what the traveller is exploring
// (destination) and where they physically are (device location, with freshness/accuracy).
export default function ContextBar({ destination, device, here, onExploreHere, onChangeDestination, onClearDestination, onEnableLocation }) {
  const location = device.location
  let locationText = 'Location off'
  if (device.status === 'locating') locationText = 'Finding you…'
  else if (location) locationText = `You${here?.name ? ` · ${here.name}` : ''} · ${describeFreshness(location)}${location.accuracy_m ? ` · ±${location.accuracy_m} m` : ''}`
  else if (device.status === 'denied') locationText = 'Location blocked'
  // Exploring one city while standing in another stored city: offer to switch.
  const elsewhere = destination && here?.destination_id && destination.destination_id !== here.destination_id
  return <div className="context-bar">
    <button type="button" className="context-pill destination" onClick={onChangeDestination} title="Change destination">
      <MapPinned size={15} /><span>{destination ? `Exploring ${destination.name}` : 'Choose destination'}</span>
    </button>
    {destination && <button type="button" className="context-clear" aria-label="Stop exploring this destination" onClick={onClearDestination}><X size={14} /></button>}
    <button type="button" className={`context-pill ${location ? 'live' : ''}`} onClick={location ? undefined : onEnableLocation} title={location ? 'Using your device location' : 'Use my location'}>
      <LocateFixed size={15} /><span>{locationText}</span>
    </button>
    {elsewhere && <button type="button" className="context-pill live" onClick={onExploreHere} title={`Explore ${here.name} instead`}>
      <Navigation size={15} /><span>You're in {here.name} · explore here</span>
    </button>}
  </div>
}
