import { LocateFixed, MapPinned, X } from 'lucide-react'
import { describeFreshness } from '../hooks/useDeviceLocation'

// Always shows the two separate pieces of geographic state: what the traveller is exploring
// (destination) and where they physically are (device location, with freshness/accuracy).
export default function ContextBar({ destination, device, onChangeDestination, onClearDestination, onEnableLocation }) {
  const location = device.location
  let locationText = 'Location off'
  if (device.status === 'locating') locationText = 'Finding you…'
  else if (location) locationText = `You · ${describeFreshness(location)}${location.accuracy_m ? ` · ±${location.accuracy_m} m` : ''}`
  else if (device.status === 'denied') locationText = 'Location blocked'
  return <div className="context-bar">
    <button type="button" className="context-pill destination" onClick={onChangeDestination} title="Change destination">
      <MapPinned size={15} /><span>{destination ? `Exploring ${destination.name}` : 'Choose destination'}</span>
    </button>
    {destination && <button type="button" className="context-clear" aria-label="Stop exploring this destination" onClick={onClearDestination}><X size={14} /></button>}
    <button type="button" className={`context-pill ${location ? 'live' : ''}`} onClick={location ? undefined : onEnableLocation} title={location ? 'Using your device location' : 'Use my location'}>
      <LocateFixed size={15} /><span>{locationText}</span>
    </button>
  </div>
}
